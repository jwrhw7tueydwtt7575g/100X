"""Twilio webhook signature validation.

The IVR webhooks are public URLs with no other authentication. Without this,
anyone who learns the URL can drive the phone assistant: burn LLM spend, fill
the transcript table, and — because digit 9 dispatches an emergency — send
responders to fabricated locations. That last one is why this is not optional.

Twilio signs each request as
`base64(hmac_sha1(auth_token, url + "".join(k + v for k, v in sorted(params))))`
and sends it as `X-Twilio-Signature`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import structlog
from fastapi import HTTPException, Request, status

from app.config import settings

log = structlog.get_logger(__name__)

SIGNATURE_HEADER = "X-Twilio-Signature"


def expected_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def is_valid(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    return hmac.compare_digest(expected_signature(url, params, auth_token), signature)


def webhook_url(request: Request) -> str:
    """The URL Twilio signed.

    Behind a proxy `request.url` is usually `http://` on an internal host,
    which would never match the `https://` URL Twilio actually called — hence
    `IVR_PUBLIC_BASE_URL`.
    """
    if settings.ivr_public_base_url:
        base = settings.ivr_public_base_url.rstrip("/")
        url = f"{base}{request.url.path}"
        return f"{url}?{request.url.query}" if request.url.query else url
    return str(request.url)


async def verify_twilio_request(request: Request) -> dict[str, str]:
    """Validate the signature and return the parsed form body.

    The body is returned because reading it twice is not possible — the route
    handlers take the parsed form from here rather than re-reading the stream.
    """
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    if not settings.ivr_validate_signature:
        log.warning(
            "twilio_signature_check_disabled",
            detail="IVR_VALIDATE_SIGNATURE is false — never do this in production",
        )
        return params

    if not settings.twilio_auth_token:
        # Fail closed. An unauthenticated endpoint that can dispatch emergency
        # responders is worse than a phone line that is down.
        log.error("twilio_auth_token_missing", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="voice channel is not configured",
        )

    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not signature or not is_valid(
        webhook_url(request), params, signature, settings.twilio_auth_token
    ):
        log.error(
            "twilio_signature_rejected",
            path=request.url.path,
            call_sid=params.get("CallSid"),
            has_signature=bool(signature),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="invalid Twilio signature"
        )

    return params
