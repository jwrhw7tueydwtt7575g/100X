"""SMS delivery for login OTPs.

Three providers, chosen with `SMS_PROVIDER`:

* `console`  — logs the message. The default, and what local development uses.
* `fast2sms` — India-only, the cheapest route for +91 numbers and the one this
  service is built around (the phone validator only accepts +91).
* `twilio`   — international fallback, called over its REST API so the SDK is
  not a dependency.

Delivery failures never raise: a pilgrim whose SMS did not arrive can retry,
and the OTP row is already committed. The caller decides what to tell them.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

FAST2SMS_URL = "https://www.fast2sms.com/dev/bulkV2"
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def otp_message(code: str, minutes: int) -> str:
    return (
        f"{code} is your WariVerse verification code. "
        f"It expires in {minutes} minutes. Do not share it with anyone."
    )


async def send_control_room_alert(
    phone_number: str,
    *,
    sos_id: str,
    emergency_type: str,
    latitude: float,
    longitude: float,
    nearest: str,
) -> bool:
    """Text the control room about a new emergency.

    Coordinates are included as a maps link because a responder reading this on
    a phone needs to navigate, not transcribe numbers.
    """
    message = (
        f"WariVerse SOS {sos_id[:8]} · {emergency_type}\n"
        f"Location: https://maps.google.com/?q={latitude},{longitude}\n"
        f"Nearest post: {nearest}"
    )
    provider = settings.sms_provider.lower()

    if provider == "fast2sms":
        # The OTP route renders a fixed template, so free text needs the `q`
        # (quick/transactional) route instead.
        return await _send_fast2sms_text(phone_number, message)
    if provider == "twilio":
        return await _send_twilio(phone_number, message)
    return _send_console(phone_number, sos_id, message)


async def send_otp_sms(phone_number: str, code: str) -> bool:
    """Deliver an OTP. Returns True when the provider accepted it."""
    minutes = max(1, settings.otp_ttl_seconds // 60)
    message = otp_message(code, minutes)
    provider = settings.sms_provider.lower()

    if provider == "fast2sms":
        return await _send_fast2sms(phone_number, code)
    if provider == "twilio":
        return await _send_twilio(phone_number, message)
    return _send_console(phone_number, code, message)


# --- providers --------------------------------------------------------------


def _send_console(phone_number: str, code: str, message: str) -> bool:
    """Development sink.

    The code is printed only when the API is also returning it as `demo_otp`
    (i.e. outside production), so this never adds an exposure that the response
    body did not already have. In production it logs the delivery, not the code.
    """
    if settings.expose_debug_otp:
        print(f"[WariVerse OTP] {phone_number}: {message}", flush=True)
        log.info("otp_console_delivery", phone=_mask(phone_number), otp=code)
    else:
        log.warning(
            "otp_console_delivery_in_production",
            phone=_mask(phone_number),
            detail="SMS_PROVIDER=console will not deliver anything to real users",
        )
    return True


async def _send_fast2sms(phone_number: str, code: str) -> bool:
    if not settings.fast2sms_api_key:
        log.error("fast2sms_not_configured", detail="FAST2SMS_API_KEY is unset")
        return False

    # Fast2SMS wants a bare 10-digit number, no country code.
    national = phone_number.removeprefix("+91")

    try:
        async with httpx.AsyncClient(timeout=settings.sms_timeout_seconds) as client:
            response = await client.post(
                FAST2SMS_URL,
                headers={"authorization": settings.fast2sms_api_key},
                json={
                    # `otp` route renders the provider's approved OTP template,
                    # which is what DLT registration in India requires.
                    "route": "otp",
                    "variables_values": code,
                    "numbers": national,
                },
            )
        ok = response.status_code == 200 and response.json().get("return") is True
        if not ok:
            log.error(
                "fast2sms_send_failed",
                phone=_mask(phone_number),
                status_code=response.status_code,
                body=response.text[:200],
            )
        return ok
    except (httpx.HTTPError, ValueError) as exc:
        log.error("fast2sms_send_error", phone=_mask(phone_number), error=str(exc))
        return False


async def _send_fast2sms_text(phone_number: str, message: str) -> bool:
    """Free-text message over Fast2SMS's transactional route."""
    if not settings.fast2sms_api_key:
        log.error("fast2sms_not_configured", detail="FAST2SMS_API_KEY is unset")
        return False

    national = phone_number.removeprefix("+91")
    try:
        async with httpx.AsyncClient(timeout=settings.sms_timeout_seconds) as client:
            response = await client.post(
                FAST2SMS_URL,
                headers={"authorization": settings.fast2sms_api_key},
                json={
                    "route": "q",
                    "message": message,
                    "numbers": national,
                    "flash": 0,
                },
            )
        ok = response.status_code == 200 and response.json().get("return") is True
        if not ok:
            log.error(
                "fast2sms_text_failed",
                phone=_mask(phone_number),
                status_code=response.status_code,
                body=response.text[:200],
            )
        return ok
    except (httpx.HTTPError, ValueError) as exc:
        log.error("fast2sms_text_error", phone=_mask(phone_number), error=str(exc))
        return False


async def _send_twilio(phone_number: str, message: str) -> bool:
    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_phone_number
    ):
        log.error("twilio_not_configured")
        return False

    try:
        async with httpx.AsyncClient(timeout=settings.sms_timeout_seconds) as client:
            response = await client.post(
                TWILIO_URL.format(sid=settings.twilio_account_sid),
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                data={
                    "From": settings.twilio_phone_number,
                    "To": phone_number,
                    "Body": message,
                },
            )
        ok = response.status_code in (200, 201)
        if not ok:
            log.error(
                "twilio_send_failed",
                phone=_mask(phone_number),
                status_code=response.status_code,
                body=response.text[:200],
            )
        return ok
    except httpx.HTTPError as exc:
        log.error("twilio_send_error", phone=_mask(phone_number), error=str(exc))
        return False


def _mask(phone_number: str) -> str:
    return (
        f"{phone_number[:3]}****{phone_number[-3:]}" if len(phone_number) > 6 else "****"
    )
