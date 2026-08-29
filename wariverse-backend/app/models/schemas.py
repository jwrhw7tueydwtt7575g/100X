"""Pydantic v2 request/response schemas.

Every field is snake_case and is serialised as-is — no alias generator — so the
JSON contract the mobile client sees is snake_case end to end.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- shared types -----------------------------------------------------------

Language = Literal["mr", "hi", "en", "kn", "te"]
DensityLevel = Literal["low", "moderate", "high", "critical"]
Trend = Literal["rising", "steady", "falling"]
FacilityType = Literal[
    "water",
    "toilet",
    "medical",
    "food",
    "shelter",
    "charging",
    "police",
    "lost_found_desk",
    "parking",
    "bathing_ghat",
    "information",
]
EmergencyType = Literal["medical", "lost_person", "crowd_crush", "fire", "harassment", "other"]
Intent = Literal[
    "greeting",
    "crowd",
    "facility",
    "route",
    "temple",
    "lost_found",
    "sos",
    "smalltalk",
    "unknown",
]
LostFoundStatus = Literal["open", "in_progress", "matched", "resolved", "closed"]

Latitude = Annotated[float, Field(ge=-90, le=90, description="WGS84 latitude")]
Longitude = Annotated[float, Field(ge=-180, le=180, description="WGS84 longitude")]

_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def _normalise_phone(value: str) -> str:
    """E.164-ish normalisation; bare 10-digit Indian numbers get a +91 prefix."""
    cleaned = re.sub(r"[\s\-()]", "", value.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if re.fullmatch(r"[6-9]\d{9}", cleaned):
        cleaned = "+91" + cleaned
    if not _PHONE_RE.match(cleaned):
        raise ValueError("phone must be a valid E.164 number, e.g. +919876543210")
    return cleaned


class Schema(BaseModel):
    """Base for every schema in the API."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
    )


class GeoPoint(Schema):
    lat: Latitude
    lon: Longitude
    accuracy_m: float | None = Field(default=None, ge=0, le=10_000)


# --- health -----------------------------------------------------------------


class HealthResponse(Schema):
    status: Literal["ok"] = "ok"
    version: str


class ComponentHealth(Schema):
    name: str
    status: Literal["ok", "degraded", "down", "disabled"]
    detail: str | None = None


class ReadinessResponse(Schema):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    components: list[ComponentHealth]


class ErrorDetail(Schema):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(Schema):
    error: ErrorDetail
    request_id: str | None = None


# --- auth -------------------------------------------------------------------


class OtpSendRequest(Schema):
    phone: str = Field(..., examples=["+919876543210"])
    language: Language | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalise_phone(v)


class OtpSendResponse(Schema):
    request_id: str
    phone: str
    expires_in_seconds: int
    resend_after_seconds: int
    channel: Literal["sms"] = "sms"
    # Populated only outside production, so the app can be tested without an
    # SMS gateway. Never returned when ENVIRONMENT=production.
    debug_otp: str | None = None


class OtpVerifyRequest(Schema):
    phone: str
    otp: str = Field(..., min_length=4, max_length=8, pattern=r"^\d+$")
    display_name: str | None = Field(default=None, max_length=120)
    preferred_language: Language | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalise_phone(v)


class UserOut(Schema):
    id: UUID
    phone: str
    display_name: str | None = None
    preferred_language: Language
    dindi_name: str | None = None
    created_at: datetime


class TokenResponse(Schema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in_seconds: int
    user: UserOut


# --- conversation -----------------------------------------------------------


class ConversationAction(Schema):
    """A follow-up the client can render as a button / deep link."""

    type: Literal[
        "open_map",
        "show_facilities",
        "show_crowd",
        "show_route",
        "show_temple",
        "confirm_sos",
        "open_lost_found",
        "call_number",
    ]
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConversationMessageRequest(Schema):
    text: str = Field(..., min_length=1, max_length=2000)
    session_id: UUID | None = None
    user_id: UUID | None = None
    language: Language | None = Field(
        default=None, description="Omit to auto-detect from the message script."
    )
    location: GeoPoint | None = None


class ConversationMessageResponse(Schema):
    session_id: UUID
    reply: str
    language: Language
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    actions: list[ConversationAction] = Field(default_factory=list)
    data: dict[str, Any] | None = Field(
        default=None, description="Structured payload backing the reply, if any."
    )
    requires_sos_confirmation: bool = False
    source: Literal["llm", "rules"] = "rules"
    latency_ms: int


class SosConfirmRequest(Schema):
    session_id: UUID
    confirmed: bool
    emergency_type: EmergencyType = "other"
    location: GeoPoint | None = None
    description: str | None = Field(default=None, max_length=1000)
    phone: str | None = None
    user_id: UUID | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalise_phone(v) if v else None


class SosConfirmResponse(Schema):
    session_id: UUID
    confirmed: bool
    message: str
    language: Language
    sos: SosEventResponse | None = None


# --- crowd ------------------------------------------------------------------


class AlternateZone(Schema):
    zone_id: str
    name: str
    density_level: DensityLevel
    distance_m: int | None = None


class CrowdResponse(Schema):
    zone_id: str
    zone_name: str
    zone_type: str
    density_level: DensityLevel
    people_estimate: int
    capacity: int
    occupancy_ratio: float = Field(ge=0)
    wait_minutes: int | None = None
    trend: Trend
    advice: str
    language: Language
    alternate_zones: list[AlternateZone] = Field(default_factory=list)
    source: Literal["live", "cache", "estimated"] = Field(
        description="`live` — fresh sensor snapshot; `cache` — a sensor snapshot "
        "served from Redis; `estimated` — no sensor data, this is a modelled "
        "approximation and should be presented as one."
    )
    updated_at: datetime


# --- facilities -------------------------------------------------------------


class FacilityOut(Schema):
    id: UUID | None = None
    external_id: str | None = None
    name: str
    facility_type: FacilityType
    lat: float
    lon: float
    distance_m: int
    walk_minutes: int
    address: str | None = None
    contact_phone: str | None = None
    is_open: bool
    is_24x7: bool
    opens_at: str | None = None
    closes_at: str | None = None
    capacity: int | None = None
    wheelchair_accessible: bool = False
    details: dict[str, Any] | None = None


class FacilityNearbyResponse(Schema):
    origin: GeoPoint
    radius_m: int
    count: int
    facility_types: list[FacilityType]
    language: Language
    facilities: list[FacilityOut]


# --- routes -----------------------------------------------------------------


class RouteStep(Schema):
    sequence: int
    instruction: str
    name: str
    lat: float
    lon: float
    distance_m: int
    cumulative_distance_m: int
    is_halt: bool
    landmark: str | None = None
    congestion: DensityLevel | None = None


class RouteWarning(Schema):
    zone_id: str | None = None
    severity: Literal["info", "caution", "avoid"]
    message: str


class RouteGuidanceResponse(Schema):
    route_id: str
    origin: GeoPoint
    destination_name: str
    destination: GeoPoint
    distance_km: float
    eta_minutes: int
    congestion_level: DensityLevel
    language: Language
    steps: list[RouteStep]
    warnings: list[RouteWarning] = Field(default_factory=list)
    updated_at: datetime


# --- temple -----------------------------------------------------------------


class ScheduleEntry(Schema):
    name: str
    starts_at: str
    ends_at: str | None = None
    note: str | None = None


class TempleInfoResponse(Schema):
    temple_id: str
    name: str
    deity: str
    location: GeoPoint
    address: str
    language: Language
    darshan_types: list[ScheduleEntry]
    aarti_schedule: list[ScheduleEntry]
    queue_status: DensityLevel
    live_wait_minutes: int | None = None
    dress_code: list[str]
    rules: list[str]
    facilities_on_site: list[str]
    contact_phone: str | None = None
    notices: list[str] = Field(default_factory=list)
    updated_at: datetime


# --- lost & found -----------------------------------------------------------


class LostFoundCreate(Schema):
    report_type: Literal["person", "item"]
    description: str = Field(..., min_length=5, max_length=2000)
    reporter_name: str = Field(..., min_length=2, max_length=160)
    contact_phone: str
    subject_name: str | None = Field(default=None, max_length=160)
    subject_age: int | None = Field(default=None, ge=0, le=120)
    distinguishing_marks: str | None = Field(default=None, max_length=1000)
    last_seen_location: str | None = Field(default=None, max_length=255)
    last_seen_lat: Latitude | None = None
    last_seen_lon: Longitude | None = None
    last_seen_at: datetime | None = None
    photo_url: str | None = Field(default=None, max_length=500)
    language: Language | None = None

    @field_validator("contact_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalise_phone(v)


class LostFoundResponse(Schema):
    ref_id: str
    report_type: Literal["person", "item"]
    status: LostFoundStatus
    subject_name: str | None = None
    subject_age: int | None = None
    description: str
    distinguishing_marks: str | None = None
    last_seen_location: str | None = None
    last_seen_at: datetime | None = None
    reporter_name: str
    contact_phone: str
    photo_url: str | None = None
    language: Language
    assigned_desk: str | None = None
    resolution_note: str | None = None
    helpline: str
    message: str
    created_at: datetime
    updated_at: datetime


# --- sos --------------------------------------------------------------------


class SosTriggerRequest(Schema):
    lat: Latitude
    lon: Longitude
    emergency_type: EmergencyType = "other"
    accuracy_m: float | None = Field(default=None, ge=0, le=10_000)
    user_id: UUID | None = None
    session_id: UUID | None = None
    phone: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    language: Language | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalise_phone(v) if v else None


class SosEventResponse(Schema):
    sos_id: UUID
    status: Literal["pending", "dispatched", "acknowledged", "resolved", "cancelled"]
    emergency_type: EmergencyType
    location: GeoPoint
    dispatched_to: str | None = None
    eta_minutes: int | None = None
    nearest_facility: FacilityOut | None = None
    helpline_numbers: list[str]
    message: str
    language: Language
    created_at: datetime


# Resolve the forward reference used by SosConfirmResponse.
SosConfirmResponse.model_rebuild()
