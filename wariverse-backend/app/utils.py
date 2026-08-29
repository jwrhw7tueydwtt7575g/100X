"""Small cross-cutting helpers."""

from __future__ import annotations

import random
import string
from datetime import UTC, datetime, time, timedelta, timezone

# The Wari happens entirely in Maharashtra and IST has no DST, so a fixed
# offset avoids depending on the tzdata package being present in the image.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_REF_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY3479"  # no look-alike characters


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_ist() -> datetime:
    return datetime.now(IST)


def generate_ref_id(prefix: str = "WV", length: int = 6) -> str:
    """Human-readable reference id, e.g. `WV-7KQ4XM`, safe to read over a phone."""
    body = "".join(random.choices(_REF_ALPHABET, k=length))
    return f"{prefix}-{body}"


def generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def is_open_now(
    opens_at: str | None, closes_at: str | None, is_24x7: bool, at: datetime | None = None
) -> bool:
    """Opening-hours check in IST, handling windows that cross midnight."""
    if is_24x7:
        return True

    start = parse_hhmm(opens_at)
    end = parse_hhmm(closes_at)
    if start is None or end is None:
        # Unknown hours: assume open rather than sending a pilgrim away.
        return True

    current = (at or now_ist()).astimezone(IST).time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end
