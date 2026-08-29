"""Pydantic v2 request/response schemas.

Every field is snake_case and is serialised as-is — no alias generator — so the
JSON contract the mobile client sees is snake_case end to end.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

# --- shared types -----------------------------------------------------------

Language = Literal["mr", "hi", "en", "kn", "te"]
# Matches the `status` vocabulary stored in crowd_density_readings, so the API
# and the ingestion pipeline never disagree about what a level is called.
DensityLevel = Literal["LOW", "MODERATE", "HIGH", "VERY_HIGH"]
Trend = Literal["rising", "steady", "falling"]
# What `/api/facilities/nearby` exposes.
FacilityCategory = Literal["medical", "water", "toilet", "rest", "food", "accommodation"]
# Internal types, a superset: SOS routes responders to police and lost & found
# desks, which pilgrims never search for by category.
FacilityType = Literal[
    "medical", "water", "toilet", "rest", "food", "accommodation",
    "police", "lost_found_desk",
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
LostFoundStatus = Literal["OPEN", "IN_PROGRESS", "MATCHED", "RESOLVED", "CLOSED"]
IncidentType = Literal["PERSON", "ITEM"]
SosStatus = Literal["PENDING", "ACTIVATED", "RESOLVED"]
Channel = Literal["app", "ivr"]

Latitude = Annotated[float, Field(ge=-90, le=90, description="WGS84 latitude")]
Longitude = Annotated[float, Field(ge=-180, le=180, description="WGS84 longitude")]

_INDIAN_PHONE_RE = re.compile(r"^\+91[6-9]\d{9}$")


def _normalise_phone(value: str) -> str:
    """Normalise to `+91XXXXXXXXXX`.

    WariVerse serves one pilgrimage in one Indian state, so only +91 numbers
    are accepted. Indian mobile numbers are 10 digits starting 6-9; the common
    ways users type them (spaces, dashes, a leading 0, 91 or 0091) are all
    accepted and normalised rather than rejected.
    """
    cleaned = re.sub(r"[\s\-()]", "", value.strip())

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+91"):
        national = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        national = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        national = cleaned[1:]
    else:
        national = cleaned.lstrip("+")

    candidate = f"+91{national}"
    if not _INDIAN_PHONE_RE.match(candidate):
        raise ValueError(
            "phone_number must be an Indian mobile number, e.g. +919876543210 "
            "(10 digits starting 6-9)"
        )
    return candidate


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
    phone_number: str = Field(..., examples=["+919876543210"])

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalise_phone(v)


class OtpSendResponse(Schema):
    success: bool = True
    message: str = "OTP sent"
    # Returned only outside production so the app can be exercised without an
    # SMS gateway. Always null when ENVIRONMENT=production.
    demo_otp: str | None = None


class OtpVerifyRequest(Schema):
    phone_number: str = Field(..., examples=["+919876543210"])
    otp: str = Field(..., min_length=4, max_length=8, pattern=r"^\d+$")

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalise_phone(v)


class UserOut(Schema):
    id: UUID
    phone_number: str
    name: str | None = None
    language: Language


class UserProfile(UserOut):
    """`/auth/me` — the fuller record, for a profile screen."""

    is_verified: bool
    created_at: datetime
    updated_at: datetime


class TokenResponse(Schema):
    success: bool = True
    token: str
    user: UserOut


class ProfileUpdateRequest(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: Literal["mr", "hi", "en"] | None = None


class ProfileUpdateResponse(Schema):
    success: bool = True
    user: UserProfile


# --- conversation -----------------------------------------------------------


WidgetType = Literal[
    "crowd_density",
    "congestion_forecast",
    "route_guidance",
    "nearby_facility",
    "temple_info",
    "lost_and_found",
    "sos",
    "human_escalation",
]


class Widget(Schema):
    """A card the app renders beneath the reply.

    `data` is left untyped because each widget type has its own shape — see the
    builders in `app/services/llm_orchestrator.py`. Keys are snake_case, and
    `updated_at` is a rendered phrase ("2 min ago"), not a timestamp, because
    the frontend prints it verbatim.
    """

    type: WidgetType
    data: dict[str, Any]


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


class ClientSchema(Schema):
    """Request base that accepts snake_case or camelCase field names.

    The frontend is TypeScript and may send either; rejecting `isVoice` because
    the model declares `is_voice` would be a pointless failure. Responses are
    always serialised snake_case.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
        alias_generator=AliasGenerator(
            validation_alias=lambda name: AliasChoices(name, _camel(name))
        ),
    )


class ConversationMessageRequest(ClientSchema):
    """Matches the frontend's `MessageRequest`."""

    message: str = Field(
        ..., min_length=1, max_length=2000, examples=["How crowded is Gate 3?"]
    )
    # An opaque client string, not a UUID — the frontend ships a literal.
    session_id: str | None = Field(default=None, max_length=200, examples=["wariverse-session"])
    language: Language | None = Field(
        default=None, description="Omit to auto-detect from the message script."
    )
    is_voice: bool = Field(
        default=False, description="True when the text came from speech input."
    )
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    channel: Channel = "app"

    @property
    def location(self) -> GeoPoint | None:
        if self.latitude is None or self.longitude is None:
            return None
        return GeoPoint(lat=self.latitude, lon=self.longitude)


class ConversationMessageResponse(Schema):
    """Matches the frontend's `ConversationResponse`."""

    session_id: str = Field(description="Echoed back exactly as the client sent it.")
    message_id: str = Field(examples=["assistant-1735689600123"])
    language: Language
    response_text: str
    widgets: list[Widget] = Field(default_factory=list)


class SosConfirmRequest(ClientSchema):
    """Calling this endpoint *is* the confirmation.

    `confirmed` is optional and defaults to true so the documented body
    `{session_id, language}` works as-is; pass `false` to cancel.
    """

    session_id: str = Field(max_length=200, examples=["wariverse-session"])
    language: Language | None = None
    confirmed: bool = True
    emergency_type: EmergencyType = "other"
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    description: str | None = Field(default=None, max_length=1000)
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalise_phone(v) if v else None

    @property
    def location(self) -> GeoPoint | None:
        if self.latitude is None or self.longitude is None:
            return None
        return GeoPoint(lat=self.latitude, lon=self.longitude)


# --- crowd ------------------------------------------------------------------


class AlternateZone(Schema):
    zone_id: str
    name: str
    density_level: DensityLevel
    distance_m: int | None = None


class CrowdResponse(Schema):
    """One zone's current crowding, as the frontend renders it."""

    zone_id: str
    zone_name: str
    density: int = Field(
        ge=0, le=100, description="Occupancy as a percentage of zone capacity."
    )
    status: DensityLevel
    latitude: float
    longitude: float
    updated_at: str = Field(
        description="A rendered phrase such as `2 min ago`, not a timestamp.",
        examples=["2 min ago"],
    )


class ForecastPoint(Schema):
    time: str = Field(examples=["10 AM"])
    value: int = Field(ge=0, le=100)


class CrowdForecastResponse(Schema):
    zone_id: str
    zone_name: str
    points: list[ForecastPoint]
    recommendation: str
    updated_at: str = Field(examples=["Updated just now"])


class AdminCrowdUpdate(ClientSchema):
    density: int = Field(ge=0, le=100)
    source: Literal["camera", "manual", "model"] = "manual"


# --- facilities -------------------------------------------------------------


class FacilityOut(Schema):
    """One place, as the frontend renders it."""

    id: str = Field(examples=["fac-001"])
    # The wider FacilityType, not FacilityCategory: SOS dispatch resolves police
    # posts and lost & found desks through this same model, even though the
    # /nearby query only ever asks for the six pilgrim-facing categories.
    category: FacilityType
    name: str
    distance: str = Field(
        description="Rendered for display, e.g. `0.8 km`.", examples=["0.8 km"]
    )
    latitude: float
    longitude: float
    availability: str = Field(
        description="Open/closed plus who staffs it.",
        examples=["Open · Volunteer staffed"],
    )
    contact: str | None = None
    # Kept for the orchestrator's tool summaries; not part of the card.
    distance_m: int = Field(exclude=True, default=0)
    walk_minutes: int = Field(exclude=True, default=0)
    is_open: bool = Field(exclude=True, default=True)


class FacilityNearbyResponse(Schema):
    facilities: list[FacilityOut]


# --- routes -----------------------------------------------------------------


class Waypoint(Schema):
    latitude: float
    longitude: float


class LabelledPoint(Waypoint):
    label: str


class RouteGuidanceResponse(Schema):
    """Matches `RouteWidget` in the frontend's domain.ts."""

    origin: LabelledPoint
    destination: LabelledPoint
    route_coordinates: list[Waypoint]
    estimated_time: str = Field(examples=["18 min walk"])
    distance: str = Field(examples=["1.2 km"])
    avoid_areas: list[str] = Field(
        default_factory=list, examples=[["Gate 3 — high congestion"]]
    )
    # Which of the three precomputed routes was chosen; useful in logs and for
    # the orchestrator's tool summary, not shown on the card.
    route_id: str = Field(exclude=True, default="")


# --- temple -----------------------------------------------------------------


class TempleInfoResponse(Schema):
    """Matches `TempleInfoWidget` in the frontend."""

    title: str
    timings: str = Field(examples=["6:00 AM – 11:00 PM"])
    rituals: list[str]
    events: list[str]
    description: str


class TempleInfoUpdate(ClientSchema):
    """Every field optional — an operator fixing one timing shouldn't resend all."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    timings: str | None = Field(default=None, min_length=1, max_length=120)
    rituals: list[str] | None = None
    events: list[str] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    language: Language = "en"


# --- lost & found -----------------------------------------------------------


class LostFoundCreate(ClientSchema):
    incident_type: IncidentType
    description: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        examples=["65 year old woman, white saree, lost near Gate 2"],
    )
    reporter_phone: str
    last_seen_location: str | None = Field(default=None, max_length=255)
    session_id: str | None = Field(default=None, max_length=200)
    language: Language | None = None

    @field_validator("reporter_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _normalise_phone(v)


class LostFoundResponse(Schema):
    """What the pilgrim sees after filing, and on lookup.

    `status` is the human label ("Searching"), not the stored enum — the app
    prints it verbatim.
    """

    reference_id: str = Field(examples=["WF-2026-00124"])
    status: str = Field(examples=["Searching"])
    next_action: str
    message: str
    # Present on lookup; harmless extras on create.
    incident_type: IncidentType | None = None
    description: str | None = None
    last_seen_location: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    session_id: str
    status: SosStatus
    emergency_type: EmergencyType
    location: GeoPoint
    dispatched_to: str | None = None
    eta_minutes: int | None = None
    nearest_facility: FacilityOut | None = None
    helpline_numbers: list[str]
    message: str
    language: Language
    created_at: datetime


