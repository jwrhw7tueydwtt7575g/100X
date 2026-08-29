"""Conversation session state.

Three layers, deliberately:

* **Redis** holds the hot state under a TTL — the read on every chat turn.
* **`sessions.context_json`** is the durable copy of that same state, so a
  conversation survives a Redis flush or a pod restart.
* **`messages`** is the append-only transcript the control room reads.

If Redis is down we fall back to a bounded in-process dict: enough for one
worker to keep a conversation coherent, explicitly not a correctness guarantee
across replicas.
"""

from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import Message, Session
from app.redis_client import get_redis
from app.utils import now_utc

log = structlog.get_logger(__name__)

SESSION_PREFIX = "wv:session:"
TOKEN_PREFIX = "wv:session:token:"
MAX_HISTORY = 20
_MEMORY_LIMIT = 2000

# Fallback store used only when Redis is unavailable.
_memory_store: OrderedDict[str, dict[str, Any]] = OrderedDict()


def new_session_token() -> str:
    """Opaque handle for resuming a session outside the app (IVR gateway)."""
    return secrets.token_urlsafe(32)[:64]


def session_key(client_key: str, user_id: UUID | None = None) -> str:
    """Map a client-supplied session id onto a storage key.

    The frontend sends a literal string (`"wariverse-session"`), not a UUID, so
    it is stored in `sessions.session_token` and the internal UUID stays the
    primary key.

    When the caller is authenticated the key is scoped to their user id.
    Without that, every install sending the same literal would land in ONE
    shared session — one pilgrim's "yes" could confirm another's emergency, and
    transcripts would mix. See the warning in `app/routers/conversation.py`.
    """
    scoped = f"u:{user_id}:{client_key}" if user_id else client_key
    if len(scoped) > 64:
        return sha256(scoped.encode()).hexdigest()  # 64 chars exactly
    return scoped


@dataclass
class SessionState:
    session_id: UUID
    user_id: UUID | None = None
    session_token: str = field(default_factory=new_session_token)
    # What the client sent as `session_id` and expects echoed back verbatim.
    client_key: str | None = None
    language: str = "mr"
    channel: str = "app"
    last_intent: str | None = None
    pending_sos: bool = False
    escalation: dict[str, Any] | None = None
    # IVR call metadata, set when the call ends.
    call: dict[str, Any] | None = None
    # In-app IVR menu position, e.g. {"state": "menu"}.
    ivr: dict[str, Any] | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    is_new: bool = False

    # --- (de)serialisation -------------------------------------------------

    def to_context_json(self) -> dict[str, Any]:
        """The shape stored in `sessions.context_json`."""
        return {
            "history": self.messages[-MAX_HISTORY:],
            "pending_sos": self.pending_sos,
            "escalation": self.escalation,
            "call": self.call,
            "ivr": self.ivr,
            "last_intent": self.last_intent,
            "last_location": (
                {"lat": self.last_lat, "lon": self.last_lon}
                if self.last_lat is not None and self.last_lon is not None
                else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "session_token": self.session_token,
            "client_key": self.client_key,
            "language": self.language,
            "channel": self.channel,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            **self.to_context_json(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SessionState:
        location = raw.get("last_location") or {}
        return cls(
            session_id=UUID(raw["session_id"]),
            user_id=UUID(raw["user_id"]) if raw.get("user_id") else None,
            session_token=raw.get("session_token") or new_session_token(),
            client_key=raw.get("client_key"),
            language=raw.get("language", settings.default_language),
            channel=raw.get("channel", "app"),
            last_intent=raw.get("last_intent"),
            pending_sos=bool(raw.get("pending_sos", False)),
            escalation=raw.get("escalation"),
            call=raw.get("call"),
            ivr=raw.get("ivr"),
            last_lat=location.get("lat"),
            last_lon=location.get("lon"),
            messages=list(raw.get("history") or []),
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

    async def resolve(
        self,
        client_key: str | None,
        user_id: UUID | None = None,
        language: str | None = None,
        channel: str = "app",
    ) -> SessionState:
        """Find or create the session behind a client-supplied session id.

        The client key is opaque — a UUID from an older build, or the literal
        the frontend ships with. It is looked up in `sessions.session_token`.
        """
        if not client_key:
            state = SessionState(
                session_id=uuid4(),
                user_id=user_id,
                language=language or settings.default_language,
                channel=channel,
                is_new=True,
            )
            state.client_key = str(state.session_id)
            state.session_token = session_key(state.client_key, user_id)
            await self._persist_session_row(state)
            await self.save(state)
            return state

        token = session_key(client_key, user_id)
        state = await self._load_by_token(token)

        if state is not None:
            state.client_key = client_key
            if language:
                state.language = language
            if user_id and state.user_id is None:
                state.user_id = user_id
            return state

        state = SessionState(
            session_id=uuid4(),
            user_id=user_id,
            session_token=token,
            language=language or settings.default_language,
            channel=channel,
            is_new=True,
        )
        state.client_key = client_key
        await self._persist_session_row(state)
        await self.save(state)
        await self._remember_token(token, state.session_id)
        return state

    async def get_or_create(
        self,
        session_id: UUID | None = None,
        user_id: UUID | None = None,
        language: str | None = None,
        channel: str = "app",
    ) -> SessionState:
        if session_id is not None:
            state = await self._load(session_id)
            if state is None:
                state = await self._load_from_db(session_id)
            if state is not None:
                if language:
                    state.language = language
                if user_id and state.user_id is None:
                    state.user_id = user_id
                return state

        # An unknown session id is honoured rather than replaced: the client
        # generated it and will keep using it, and a lost cache entry must not
        # silently split one conversation into two.
        state = SessionState(
            session_id=session_id or uuid4(),
            user_id=user_id,
            language=language or settings.default_language,
            channel=channel,
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
                await client.set(key, json.dumps(payload), ex=settings.session_ttl_seconds)
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
        widgets: list[dict[str, Any]] | None = None,
        is_voice: bool = False,
    ) -> UUID | None:
        """Append one exchange to the hot state and the durable transcript.

        Returns the assistant message's id, or None when Postgres is down and
        only the in-memory/Redis copy exists.
        """
        timestamp = now_utc().isoformat()
        state.messages.append({"role": "user", "content": user_text, "at": timestamp})
        state.messages.append(
            {"role": "assistant", "content": assistant_text, "at": timestamp, "intent": intent}
        )
        state.messages = state.messages[-MAX_HISTORY:]
        state.last_intent = intent or state.last_intent

        await self.save(state)
        return await self._persist_messages(
            state, user_text, assistant_text, widgets=widgets, is_voice=is_voice
        )

    async def set_pending_sos(self, state: SessionState, pending: bool) -> None:
        state.pending_sos = pending
        await self.save(state)
        await self._sync_context(state)

    async def finish_call(self, state: SessionState, metadata: dict[str, Any]) -> None:
        """Close an IVR session, recording how the call ended.

        The transcript is already in `messages` — turns are written as they
        happen — so this only stores call metadata for the control room.
        """
        state.call = metadata
        await self.save(state)
        await self._sync_context(state)

    async def sync_context(self, state: SessionState) -> None:
        """Write the hot state through to `sessions.context_json`.

        Callers that change something a stale copy could act on — the IVR menu
        position most of all — should use this rather than `save()` alone. If
        Redis were lost while the durable copy still said `sos_confirm`, the
        pilgrim's next keypress could dispatch an emergency they had moved on
        from.
        """
        await self._sync_context(state)

    async def escalate(self, state: SessionState, reason: str) -> dict[str, Any]:
        """Flag a session for a human volunteer to pick up.

        Recorded on `context_json` so the control-room dashboard can query for
        waiting pilgrims without a schema change.
        """
        record = {
            "status": "WAITING",
            "reason": reason[:500],
            "requested_at": now_utc().isoformat(),
        }
        state.escalation = record
        await self.save(state)
        await self._sync_context(state)
        log.warning(
            "session_escalated",
            session_id=str(state.session_id),
            reason=reason[:200],
        )
        return record

    # --- internals ---------------------------------------------------------

    async def _load_by_token(self, token: str) -> SessionState | None:
        """Resolve a session_token to its state: Redis pointer, then Postgres."""
        client = get_redis()
        if client is not None:
            try:
                if raw := await client.get(f"{TOKEN_PREFIX}{token}"):
                    if state := await self._load(UUID(raw)):
                        return state
            except (RedisError, OSError, ValueError) as exc:
                log.warning("session_token_lookup_failed", error=str(exc))

        if self.db is None:
            # No database: fall back to the in-process store so a single worker
            # still keeps the conversation coherent.
            for payload in _memory_store.values():
                if payload.get("session_token") == token:
                    try:
                        return SessionState.from_dict(payload)
                    except (KeyError, ValueError):
                        continue
            return None

        try:
            row = (
                await self.db.execute(select(Session).where(Session.session_token == token))
            ).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001
            log.warning("session_token_db_lookup_failed", error=str(exc))
            return None

        if row is None:
            return None

        state = await self._load(row.id) or self._state_from_row(row)
        await self._remember_token(token, row.id)
        return state

    async def _remember_token(self, token: str, session_id: UUID) -> None:
        client = get_redis()
        if client is None:
            return
        try:
            await client.set(
                f"{TOKEN_PREFIX}{token}", str(session_id), ex=settings.session_ttl_seconds
            )
        except (RedisError, OSError) as exc:
            log.warning("session_token_cache_failed", error=str(exc))

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

    async def _load_from_db(self, session_id: UUID) -> SessionState | None:
        """Rehydrate from `context_json` after a Redis flush or restart."""
        row = await self._session_row(session_id)
        if row is None:
            return None
        state = self._state_from_row(row)
        await self.save(state)
        return state

    @staticmethod
    def _state_from_row(row: Session) -> SessionState:
        context = row.context_json or {}
        location = context.get("last_location") or {}
        return SessionState(
            session_id=row.id,
            user_id=row.user_id,
            session_token=row.session_token,
            language=row.language,
            channel=row.channel,
            last_intent=context.get("last_intent"),
            pending_sos=bool(context.get("pending_sos", False)),
            escalation=context.get("escalation"),
            call=context.get("call"),
            ivr=context.get("ivr"),
            last_lat=location.get("lat"),
            last_lon=location.get("lon"),
            messages=list(context.get("history") or []),
            created_at=row.created_at,
            updated_at=row.last_active_at,
        )

    async def _session_row(self, session_id: UUID) -> Session | None:
        if self.db is None:
            return None
        try:
            return (
                await self.db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001
            log.warning("session_row_read_failed", error=str(exc))
            return None

    async def ensure_row(self, state: SessionState) -> bool:
        """Guarantee a `sessions` row exists — SOS events FK to it."""
        if self.db is None:
            return False
        if await self._session_row(state.session_id) is not None:
            return True
        return await self._persist_session_row(state)

    async def _persist_session_row(self, state: SessionState) -> bool:
        if self.db is None:
            return False
        try:
            if await self._session_row(state.session_id) is not None:
                return True
            self.db.add(
                Session(
                    id=state.session_id,
                    user_id=state.user_id,
                    session_token=state.session_token,
                    language=state.language,
                    channel=state.channel,
                    context_json=state.to_context_json(),
                )
            )
            await self.db.commit()
            return True
        except Exception as exc:  # noqa: BLE001 — chat must work without Postgres
            log.warning("session_row_persist_failed", error=str(exc))
            await self.db.rollback()
            return False

    async def _sync_context(self, state: SessionState) -> None:
        if self.db is None:
            return
        try:
            row = await self._session_row(state.session_id)
            if row is not None:
                row.context_json = state.to_context_json()
                row.language = state.language
                await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("session_context_persist_failed", error=str(exc))
            await self.db.rollback()

    async def _persist_messages(
        self,
        state: SessionState,
        user_text: str,
        assistant_text: str,
        *,
        widgets: list[dict[str, Any]] | None,
        is_voice: bool,
    ) -> UUID | None:
        if self.db is None:
            return None
        try:
            row = await self._session_row(state.session_id)
            if row is None:
                row = Session(
                    id=state.session_id,
                    user_id=state.user_id,
                    session_token=state.session_token,
                    language=state.language,
                    channel=state.channel,
                )
                self.db.add(row)
                await self.db.flush()

            row.language = state.language
            row.context_json = state.to_context_json()

            assistant_message = Message(
                session_id=state.session_id,
                role="assistant",
                content=assistant_text,
                language=state.language,
                widgets_json=widgets or None,
                is_voice=False,
            )
            self.db.add_all(
                [
                    Message(
                        session_id=state.session_id,
                        role="user",
                        content=user_text,
                        language=state.language,
                        is_voice=is_voice,
                    ),
                    assistant_message,
                ]
            )
            await self.db.commit()
            return assistant_message.id
        except Exception as exc:  # noqa: BLE001
            log.warning("session_messages_persist_failed", error=str(exc))
            await self.db.rollback()
            return None
