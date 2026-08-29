"""Small cross-cutting helpers."""

from __future__ import annotations

import random
import secrets
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
    """Cryptographically random code.

    `secrets`, not `random`: Mersenne Twister output is reconstructable from
    enough observed values, and here that would mean predicting login codes.
    """
    return "".join(secrets.choice("0123456789") for _ in range(length))


_RELATIVE_TIME: dict[str, dict[str, str]] = {
    "mr": {
        "now": "आत्ताच", "min": "{n} मिनिटांपूर्वी", "hr": "{n} तासांपूर्वी",
        "day": "{n} दिवसांपूर्वी",
    },
    "hi": {
        "now": "अभी", "min": "{n} मिनट पहले", "hr": "{n} घंटे पहले",
        "day": "{n} दिन पहले",
    },
    "en": {"now": "just now", "min": "{n} min ago", "hr": "{n} hr ago", "day": "{n} days ago"},
}


def humanize_age(moment: datetime, language: str = "en", now: datetime | None = None) -> str:
    """Relative age of a timestamp, e.g. "2 min ago".

    The frontend renders `updated_at` verbatim, so it has to be a phrase rather
    than an ISO timestamp — and a localized one, since a Marathi speaker should
    not be reading English next to a Marathi zone name.
    """
    words = _RELATIVE_TIME.get(language, _RELATIVE_TIME["en"])
    reference = now or now_utc()

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = max(0, int((reference - moment).total_seconds()))

    if seconds < 60:
        return words["now"]
    if seconds < 3600:
        return words["min"].format(n=seconds // 60)
    if seconds < 86_400:
        return words["hr"].format(n=seconds // 3600)
    return words["day"].format(n=seconds // 86_400)


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
