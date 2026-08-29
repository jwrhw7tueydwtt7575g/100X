"""Conversation session state.

Redis holds the hot state (recent turns, language, pending SOS flag) with a TTL;
Postgres keeps the durable transcript for audit and for follow-up by the control
room. If Redis is down we fall back to a bounded in-process dict — good enough
for a single worker to keep a conversation coherent, and explicitly not a
correctness guarantee across replicas.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import ConversationMessage, ConversationSession
from app.redis_client import get_redis
from app.utils import now_utc

log = structlog.get_logger(__name__)

SESSION_PREFIX = "wv:session:"
MAX_HISTORY = 20
_MEMORY_LIMIT = 2000

# Fallback store used only when Redis is unavailable.
_memory_store: OrderedDict[str, dict[str, Any]] = OrderedDict()


@dataclass
class SessionState:
    session_id: UUID
    user_id: UUID | None = None
    language: str = "mr"
    last_intent: str | None = None
    pending_sos: bool = False
    last_lat: float | None = None
    last_lon: float | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    is_new: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "language": self.language,
            "last_intent": self.last_intent,
            "pending_sos": self.pending_sos,
            "last_lat": self.last_lat,
            "last_lon": self.last_lon,
            "messages": self.messages[-MAX_HISTORY:],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SessionState:
        return cls(
            session_id=UUID(raw["session_id"]),
            user_id=UUID(raw["user_id"]) if raw.get("user_id") else None,
            language=raw.get("language", settings.default_language),
            last_intent=raw.get("last_intent"),
            pending_sos=bool(raw.get("pending_sos", False)),
            last_lat=raw.get("last_lat"),
            last_lon=raw.get("last_lon"),
            messages=list(raw.get("messages") or []),
            created_at=datetime.fromisoformat(raw["created_at"]),
            updated_at=datetime.fromisoformat(raw["updated_at"]),
        )

    def history_for_llm(self, turns: int) -> list[dict[str, str]]:
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.messages[-(turns * 2) :]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]


class SessionService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def get_or_create(
        self,
        session_id: UUID | None = None,
        user_id: UUID | None = None,
        language: str | None = None,
    ) -> SessionState:
        if session_id is not None:
            state = await self._load(session_id)
            if state is not None:
                if language:
                    state.language = language
                if user_id and state.user_id is None:
                    state.user_id = user_id
                return state

        # An unknown session id is honoured rather than replaced: the client
        # generated it and will keep using it, and a lost cache entry should not
        # silently split one conversation into two.
        state = SessionState(
            session_id=session_id or uuid4(),
            user_id=user_id,
            language=language or settings.default_language,
            is_new=True,
        )
        await self._persist_session_row(state)
        await self.save(state)
        return state

    async def save(self, state: SessionState) -> None:
        state.updated_at = now_utc()
        payload = state.to_dict()
        key = f"{SESSION_PREFIX}{state.session_id}"

        client = get_redis()
        if client is not None:
            try:
                await client.set(
                    key, json.dumps(payload), ex=settings.session_ttl_seconds
                )
                return
            except (RedisError, OSError) as exc:
                log.warning("session_redis_write_failed", error=str(exc))

        _memory_store[key] = payload
        _memory_store.move_to_end(key)
        while len(_memory_store) > _MEMORY_LIMIT:
            _memory_store.popitem(last=False)

    async def record_turn(
        self,
        state: SessionState,
        user_text: str,
        assistant_text: str,
        *,
        intent: str | None = None,
        confidence: float | None = None,
        latency_ms: int | None = None,
        model: str | None = None,
    ) -> None:
        """Append one exchange to the hot state and to the durable transcript."""
        timestamp = now_utc().isoformat()
        state.messages.append({"role": "user", "content": user_text, "at": timestamp})
        state.messages.append(
            {"role": "assistant", "content": assistant_text, "at": timestamp, "intent": intent}
        )
        state.messages = state.messages[-MAX_HISTORY:]
        state.last_intent = intent or state.last_intent

        await self.save(state)
        await self._persist_messages(
            state,
            user_text,
            assistant_text,
            intent=intent,
            confidence=confidence,
            latency_ms=latency_ms,
            model=model,
        )

    async def set_pending_sos(self, state: SessionState, pending: bool) -> None:
        state.pending_sos = pending
        await self.save(state)
        if self.db is None:
            return
        try:
            row = await self._session_row(state.session_id)
            if row is not None:
                row.pending_sos = pending
                await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("session_sos_flag_persist_failed", error=str(exc))
            await self.db.rollback()

    # --- internals ---------------------------------------------------------

    async def _load(self, session_id: UUID) -> SessionState | None:
        key = f"{SESSION_PREFIX}{session_id}"

        client = get_redis()
        raw: str | None = None
        if client is not None:
            try:
                raw = await client.get(key)
            except (RedisError, OSError) as exc:
                log.warning("session_redis_read_failed", error=str(exc))

        payload: dict[str, Any] | None = None
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("session_payload_corrupt", session_id=str(session_id))
        else:
            payload = _memory_store.get(key)

        if payload is None:
            return None

        try:
            return SessionState.from_dict(payload)
        except (KeyError, ValueError) as exc:
            log.warning("session_payload_invalid", session_id=str(session_id), error=str(exc))
            return None

    async def _session_row(self, session_id: UUID) -> ConversationSession | None:
        if self.db is None:
            return None
        return (
            await self.db.execute(
                select(ConversationSession).where(ConversationSession.id == session_id)
            )
        ).scalar_one_or_none()

    async def _persist_session_row(self, state: SessionState) -> None:
        if self.db is None:
            return
        try:
            if await self._session_row(state.session_id) is not None:
                return
            self.db.add(
                ConversationSession(
                    id=state.session_id,
                    user_id=state.user_id,
                    language=state.language,
                )
            )
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001 — chat must work without Postgres
            log.warning("session_row_persist_failed", error=str(exc))
            await self.db.rollback()

    async def _persist_messages(
        self,
        state: SessionState,
        user_text: str,
        assistant_text: str,
        *,
        intent: str | None,
        confidence: float | None,
        latency_ms: int | None,
        model: str | None,
    ) -> None:
        if self.db is None:
            return
        try:
            row = await self._session_row(state.session_id)
            if row is None:
                row = ConversationSession(
                    id=state.session_id, user_id=state.user_id, language=state.language
                )
                self.db.add(row)
                await self.db.flush()

            row.language = state.language
            row.last_intent = intent or row.last_intent
            row.last_lat = state.last_lat
            row.last_lon = state.last_lon
            row.pending_sos = state.pending_sos
            row.message_count = (row.message_count or 0) + 2

            self.db.add_all(
                [
                    ConversationMessage(
                        session_id=state.session_id,
                        role="user",
                        content=user_text,
                        language=state.language,
                        intent=intent,
                    ),
                    ConversationMessage(
                        session_id=state.session_id,
                        role="assistant",
                        content=assistant_text,
                        language=state.language,
                        intent=intent,
                        confidence=confidence,
                        latency_ms=latency_ms,
                        model=model,
                    ),
                ]
            )
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("session_messages_persist_failed", error=str(exc))
            await self.db.rollback()
