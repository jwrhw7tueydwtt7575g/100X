# WariVerse backend

FastAPI service behind **WariVerse**, a multilingual AI assistant for pilgrims
walking the Pandharpur Wari (~2 million walkers, Alandi/Dehu → Pandharpur).

It answers questions in Marathi, Hindi and English about darshan, crowd levels,
water/toilets/medical posts, walking routes and lost family members, and it can
raise an emergency SOS.

---

## Quick start

### Docker (everything, including Postgres and Redis)

```bash
cp .env.example .env          # fill in OPENAI_API_KEY and JWT_SECRET
docker compose up --build
```

The `api` container runs migrations, seeds reference data, then serves on
<http://localhost:8000>. Interactive docs: <http://localhost:8000/docs>.

### Local

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

### Tests

```bash
pytest
```

The suite runs **without** Postgres or Redis on purpose — it exercises the
degraded path that must hold when a dependency blips mid-Wari.

---

## API

Base prefix `/api`. All request and response bodies are snake_case JSON.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness → `{"status": "ok", "version": "1.0.0"}` |
| GET | `/health/ready` | Readiness with per-dependency status |
| POST | `/api/conversation/message` | Send a message to the assistant |
| POST | `/api/conversation/sos/confirm` | Confirm or cancel an emergency raised in chat |
| GET | `/api/crowd/all` | Current density for all six zones |
| GET | `/api/crowd/{zone_id}` | Current density for one zone |
| GET | `/api/crowd/{zone_id}/forecast` | Hourly projection for the next 12 hours |
| POST | `/api/admin/crowd/{zone_id}` | Manually set a density — **`X-API-Key` required** |
| GET | `/api/facilities/nearby` | Facilities near a coordinate, nearest first |
| GET | `/api/routes/guidance` | Walking route to the temple, avoiding congested zones |
| GET | `/api/temple/info` | Timings, rituals, events and visitor guidance |
| PUT | `/api/admin/temple/info` | Edit the temple card — **`X-API-Key` required** |
| POST | `/api/lost-found` | File a lost person / lost item report |
| GET | `/api/lost-found/{reference_id}` | Look up a report by reference id (`WF-2026-00124`) |
| POST | `/api/sos/trigger` | Panic button — dispatch help to a coordinate |
| POST | `/api/auth/otp/send` | Send a login OTP (max 3 per number per hour) |
| POST | `/api/auth/otp/verify` | Verify the OTP, receive a 30-day JWT |
| GET | `/api/auth/me` | Current user profile — **auth required** |
| POST | `/api/auth/profile/update` | Update name / language — **auth required** |

Every endpoint accepts `?language=mr|hi|en|kn|te`; unsupported values fall back
to `DEFAULT_LANGUAGE`. Errors use one envelope:

```json
{"error": {"code": "http_404", "message": "unknown zone_id: foo", "details": null},
 "request_id": "9f2c…"}
```

### Examples

```bash
curl -s localhost:8000/api/crowd/temple-main?language=mr

curl -s "localhost:8000/api/facilities/nearby?lat=17.6786&lon=75.33&facility_type=water&radius_m=2000"

curl -s localhost:8000/api/conversation/message \
  -H 'content-type: application/json' \
  -d '{"text":"जवळ पाणी कुठे मिळेल?","location":{"lat":17.6786,"lon":75.33}}'
```

---

## Data model

Ten tables. Seven come from the data spec:

| Table | Holds |
| --- | --- |
| `users` | pilgrims, keyed by `phone_number`; `is_verified` set on first OTP |
| `sessions` | one conversation; `context_json` is the durable LLM state, `channel` is `app` or `ivr` |
| `messages` | the transcript; `widgets_json` stores the action buttons shown with a reply |
| `crowd_density_readings` | one reading per zone: `density` 0-100 plus its `status` bucket |
| `sos_events` | emergencies; `notes` carries the dispatch detail |
| `lost_found_reports` | missing people and items, keyed by `reference_id` |
| `otp_codes` | login codes — **see the security note below** |

Three more are not in the spec but back existing endpoints: `facilities`,
`route_waypoints`, `temple_notices`.

There is no `zones` table — `crowd_density_readings` carries `zone_id`,
`zone_name` and coordinates inline, so a reading is self-describing and the
ingestion pipeline needs no join. Static zone metadata (localized names,
capacity, alternates) lives in `app/data/reference.py`.

The six monitored zones are `gate-1`, `gate-2`, `gate-3`, `temple-main`,
`bhima-ghat` and `main-road`.

**Density vocabulary.** `density` is an integer 0-100 percentage of capacity;
`status` buckets it as `LOW` (<30), `MODERATE` (30-60), `HIGH` (61-85),
`VERY_HIGH` (>85). The same four values are used in the database and in the API
so the ingestion pipeline and the app never disagree.

**Reference ids** are drawn from the `lost_found_reference_seq` Postgres
sequence, so two desks filing at the same moment cannot collide. The counter is
monotonic across years; the year in the prefix disambiguates.

---

## Auth

Phone + OTP, matching what `app/auth.tsx` sends:

```bash
# 1. request a code (only +91 mobile numbers, 10 digits starting 6-9)
curl -s localhost:8000/api/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone_number":"+919876543210"}'
# → {"success":true,"message":"OTP sent","demo_otp":"396661"}

# 2. exchange it for a token
curl -s localhost:8000/api/auth/otp/verify \
  -H 'content-type: application/json' \
  -d '{"phone_number":"+919876543210","otp":"396661"}'
# → {"success":true,"token":"eyJ…","user":{"id":…,"phone_number":…,"name":null,"language":"mr"}}

# 3. use it
curl -s localhost:8000/api/auth/me -H 'authorization: Bearer eyJ…'
curl -s localhost:8000/api/auth/profile/update -H 'authorization: Bearer eyJ…' \
  -H 'content-type: application/json' -d '{"name":"Sunita Pawar","language":"hi"}'
```

Numbers are normalised to `+91XXXXXXXXXX`, so `9876543210`, `09876543210`,
`919876543210` and `+91 98765-43210` all resolve to the same pilgrim.

`demo_otp` is returned **and** printed to the console only outside production;
when `ENVIRONMENT=production` it is `null` and no code reaches the logs.

**Tokens** are HS256, 30 days, signed with `JWT_SECRET`, and carry `user_id`,
`phone_number` and `session_id` — a session row is created at verify time, so a
token identifies both the pilgrim and the conversation they are in. Protect a
route with the `CurrentUser` dependency from `app/security.py`:

```python
from app.security import CurrentUser

@router.get("/something")
async def handler(user: CurrentUser): ...
```

**Rate limiting** is 3 codes per number per hour. Redis is the counter; when
Redis is down the count comes from `otp_codes` instead. Failing open would turn
the send endpoint into an SMS-bombing tool, so it fails closed by design.

**SMS** is chosen with `SMS_PROVIDER`:

| Value | Behaviour |
| --- | --- |
| `console` (default) | prints the message; what local development uses |
| `fast2sms` | India-only, needs `FAST2SMS_API_KEY`; uses the DLT-registered `otp` route |
| `twilio` | international, needs `TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_FROM_NUMBER` |

Twilio and Fast2SMS are called over their REST APIs with `httpx`, so neither
SDK is a dependency. Delivery failures never raise — the code row is already
committed and the pilgrim can retry.

### ⚠️ OTP codes are stored in plaintext

`otp_codes.code` holds the six digits as specified. Read access to that table —
a replica, a backup, a dump, a SQL injection — is enough to log in as any
pilgrim with a code in flight. The fix needs no schema change: store
`_hash_otp(phone, code)` (already implemented in `app/routers/auth.py`), widen
the column to `String(64)`, and compare digests on verify. Worth doing before
this handles real numbers.

---

## Facilities, routes, temple, lost & found

**Facilities.** `GET /api/facilities/nearby?lat=&lng=&category=&radius_m=1000`
returns `{"facilities": [...]}`, nearest first. `distance` and `availability`
are **rendered strings** (`"0.8 km"`, `"Open · Volunteer staffed"`) because the
app prints them verbatim. Categories: `medical`, `water`, `toilet`, `rest`,
`food`, `accommodation`. Seeded per the spec — 3 medical, 5 water, 4 toilets,
2 rest shelters, 3 langars — plus police posts and a lost & found desk that are
*not* exposed as categories but which SOS dispatch routes responders to.
`accommodation` is a valid query with **no seeded rows**: the seed spec listed
rest shelters and no overnight lodging, so it returns an empty list rather than
inventing places for pilgrims to sleep.

**Routes.** `GET /api/routes/guidance?origin_lat=&origin_lng=&dest_lat=&dest_lng=`
(destination defaults to the Vitthal temple at 17.6775, 75.3283). Three
surveyed corridors — via Gate 1, Gate 2 and Gate 3 — are precomputed in
`ROUTES`; guidance picks between them rather than free-routing, because a
shortest path through the precinct would send someone across barricades and
side lanes. Selection prefers a corridor with no HIGH/VERY_HIGH zone, then the
shortest *effective* distance, where the hop from the pilgrim onto the corridor
counts double: it is unsurveyed ground, so a corridor they already stand on
beats one that is nominally shorter but starts with a 400 m scramble. If every
route is congested it still returns the least-bad one — "no route" helps nobody.
Walking speed is **2.5 km/h**; the crowd sets the pace.

**Temple.** Content lives in the `temple_info` table, one row per language,
cached in Redis for an hour. `PUT /api/admin/temple/info` applies a partial
update and drops the cache, so a timing corrected mid-Wari is visible
immediately rather than up to an hour later.

**Lost & found.** `reference_id` is `WF-{YEAR}-{5 digits}` from a Postgres
sequence. `status` is returned as the human label the app prints (`"Searching"`,
`"Reunited"`), not the stored enum. New reports are published to the
`wv:lost_found:reports` Redis channel for the control-room dashboard — after
the row is committed, so a dead dashboard cannot cost a family their reference
number.

---

## The orchestrator

`app/services/llm_orchestrator.py` is what the conversation endpoint calls. One
turn: load the last 10 messages from Redis (`session:{session_id}:history`) →
build a system prompt for the language and channel → call gpt-4o with eight
tools → execute whatever it asks for → feed the results back → map each result
to a widget → persist and return.

```jsonc
// POST /api/conversation/message  — matches the frontend's MessageRequest
{ "session_id": "wariverse-session",   // opaque client string, not a UUID
  "language": "en",                     // "mr" | "hi" | "en"
  "message": "How crowded is Gate 3?",
  "is_voice": false,
  "latitude": 17.6778, "longitude": 75.3260,   // optional
  "channel": "app" }                            // optional, "app" | "ivr"

// → ConversationResponse
{ "session_id": "wariverse-session",
  "message_id": "assistant-1788001341084",
  "language": "en",
  "response_text": "Gate 3 is not crowded right now — a good time to go.",
  "widgets": [ { "type": "crowd_density", "data": { … } } ] }

// POST /api/conversation/sos/confirm
{ "session_id": "wariverse-session", "language": "en" }   // → sos widget, ACTIVATED
// pass "confirmed": false to cancel instead
```

Request fields are accepted in **either casing** (`is_voice` or `isVoice`);
responses are always snake_case. Every response carries an `x-request-id`
header, and every turn is logged with `session_id` and `response_time_ms`.

**Rate limit:** 30 messages per minute per session. It fails *open* when Redis
is down — the opposite of the OTP limiter, which fails closed. An OTP send
costs money and enables SMS bombing; refusing a pilgrim's question about a
medical post because the cache blipped is the worse outcome.

**Sessions.** `session_id` is an opaque client string stored in
`sessions.session_token`; the UUID primary key stays internal. An unknown id
creates an anonymous session — no auth is required for chat, because safety
information should never be gated behind a login. With an `Authorization`
header the session is linked to that user and the key is scoped to their id.

> ### ⚠️ The frontend ships one session id for every install
>
> `"wariverse-session"` is a hard-coded literal, so every **anonymous** pilgrim
> on this build shares one session: transcripts interleave, and one person's
> "yes" can confirm another's emergency. Authenticated callers are safe — their
> key is scoped by user id, and there are tests for both. The fix belongs in
> the client: generate a random id once per install and persist it.

**Tools** (all defined as JSON schemas in `TOOL_SCHEMAS`):

| Tool | Backed by |
| --- | --- |
| `get_crowd_density(zone_id)` | `CrowdService` |
| `get_congestion_forecast(zone_id, hours?)` | `CrowdService.forecast` |
| `get_route_guidance(origin_lat, origin_lng, dest_lat, dest_lng)` | `RouteService` |
| `get_nearby_facility(lat, lng, category)` | `FacilityService` |
| `get_temple_info()` | `app/data/temple.py` |
| `report_lost_found(incident_type, description, reporter_phone)` | `lost_found_reports` |
| `trigger_sos(lat, lng, emergency_type?)` | `SosService` |
| `escalate_to_human(reason)` | `sessions.context_json` |

`zone_id` is an **enum** of the six real zones, so the model cannot invent one;
if it tries anyway the tool returns an error listing the valid ids.

**Channels.** `app` gets rich replies plus widgets. `ivr` is read aloud by
text-to-speech to someone walking, so the prompt caps it at two sentences with
no lists, no digits and no markdown, and **no widgets are returned** — a phone
call has no screen.

**Widget casing.** Widget `data` keys are snake_case like everything else
(`zone_id`, `route_coordinates`, `control_room_status`). One field is not what
it looks like: `updated_at` is a **rendered, localized phrase** — `"2 min ago"`,
`"२ मिनिटांपूर्वी"` — not a timestamp, because the frontend prints it verbatim.

**An SOS is never activated by the model.** `trigger_sos` creates the emergency
as `PENDING` and the widget reports `CONFIRMATION_REQUIRED`. Only an explicit
confirmation — `POST /api/conversation/sos/confirm`, or a whole-word "yes" in
the next message — moves it to `ACTIVATED`. A false positive sends responders
away from a real emergency, so a human is always in the loop.

**Units in tool results.** What the model sees is labelled (`density_percent`,
`distance_m`, `eta_minutes`) rather than being the widget payload, so it never
has to guess whether `distance` is metres or kilometres.

---

## Design notes

**Degrade, never disappear.** Startup does not abort when Postgres or Redis is
unreachable; the API keeps serving safety information from cache or from the
bundled reference dataset, and `/health/ready` reports the real state for the
orchestrator to act on. The one deliberate exception is `POST /api/lost-found`,
which returns 503 rather than accepting a report it cannot store.

**Crowd reads are layered.** Redis (`crowd:{zone_id}`, 5 min TTL) → latest
`crowd_density_readings` row → the time-of-day curve. Readings carry a `source`
of `camera`, `manual` or `model`, so a modelled number is never mistaken for a
measurement.

**The daily curve** (`HOURLY_CURVE` in `app/services/crowd_service.py`) peaks
10:00-14:00 — the mid-morning darshan rush — with a smaller evening rise at the
aartis and a pre-dawn climb for kakad aarti. Each zone scales it by an
intensity factor, and `bhima-ghat` is shifted four hours earlier because
bathing happens at dawn. It is one hardcoded shape, **not a trained model**;
replace it once `crowd_density_readings` has real history.

**The simulator** (`app/services/crowd_simulator.py`) stands in for the CCTV
feed: every 5 minutes it samples the curve, adds ±6 of variance, limits the
move to 12 points from the previous reading (crowds build gradually; a 40-point
jump is a sensor fault, not a fact), and writes to Redis and Postgres as
`source="model"`. **Run it in one process only** — with several uvicorn workers
each would run its own copy and they would overwrite each other. Set
`CROWD_SIMULATOR_ENABLED=false` once the real feed is live.

**Forecast recommendations name the first hour crowds ease, not the quietest
hour.** The global minimum is always 1-3 AM, and "come back at 2 AM" is useless
advice to someone standing in a queue at 4 PM.

**Admin endpoints fail closed.** An unset `ADMIN_API_KEY` refuses every
request; there is no "no key configured, so allow everything" mode. Keys are
compared with `hmac.compare_digest`.

**Session state has three tiers.** Redis is the hot read on every turn,
`sessions.context_json` is the durable copy that survives a cache flush or a
restart, and `messages` is the append-only transcript. Losing Redis costs
latency, not the conversation.

**Safety wording is not generated.** SOS prompts, crowd warnings and helpline
numbers come from `app/data/i18n.py`. The model only phrases facts the tools
returned; the prompt forbids inventing timings, distances or numbers. With no
API key, a timeout or an API error, a keyword router runs the same tools and
returns the same widget shapes, so the app behaves identically minus the
phrasing.

**Tool results are never discarded.** If the model runs tools and then fails
before writing a sentence, the orchestrator describes what those tools returned
rather than starting over — otherwise a lost-person report could be filed and
its reference number never reach the pilgrim.

**Geo without PostGIS.** The Wari corridor fits in a ~250 km box, so a bounding
box on a btree `(lat, lon)` index plus a haversine in Python is accurate to
metres on stock Postgres.

**OTP codes are never written to the logs** on any path (only a masked phone
number). They are stored in `otp_codes` — see the security note above.
`_deliver_otp()` in `app/routers/auth.py` is the single seam for an SMS
provider; outside production the code is echoed as `debug_otp` so the app can
be exercised end to end.

**SOS is unauthenticated by design.** A pilgrim in trouble who never registered
must still be able to call for help. Because `sos_events.session_id` is NOT
NULL, an anonymous panic press gets a session created for it rather than being
rejected. A bearer token, when present, attributes the event so responders can
call back.

**Nullable JSONB means SQL NULL.** SQLAlchemy's JSON types write the JSON
scalar `null` for a Python `None`, which would make `WHERE widgets_json IS
NULL` skip those rows. Every nullable JSONB column uses `none_as_null=True`.

---

## Data accuracy

⚠️ `app/data/reference.py` (zones, facilities, waypoints) and
`app/data/temple.py` (timings) are **approximate placeholders** compiled from
public sources. Replace them with the surveyed dataset from the Solapur
district administration and the Mandir Samiti's published schedule before a
live Wari — pilgrims navigate on these numbers. Time-bound changes belong in
the `temple_notices` table, not in code.

The crowd feed is likewise a stand-in: wire the CCTV/drone ingestion pipeline to
write `crowd_snapshots` rows, and the `estimated` path stops being used.

---

## Configuration

All settings come from environment variables — see `.env.example` for the full
list with defaults. Required: `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`,
`JWT_SECRET`.

CORS allows `http://localhost:8081` (Expo dev client) via `CORS_ORIGINS`, and
`https://*.wariverse.app` via `CORS_ORIGIN_REGEX` — a wildcard subdomain cannot
be expressed as a literal origin, so Starlette matches it with a regex.

---

## Layout

```
app/
  main.py              FastAPI app, CORS, lifespan, error envelope, /health
  config.py            Pydantic Settings
  db.py                Async engine + session dependency
  redis_client.py      Async Redis client + JSON cache helpers
  security.py          JWT issuing/verification, bearer dependencies
  deps.py              Shared FastAPI dependencies
  utils.py             IST clock, reference ids, opening hours
  routers/             conversation, crowd, facilities, routes, temple,
                       lost_found, sos, auth
  models/              db_models.py (SQLAlchemy), schemas.py (Pydantic v2)
  services/            llm_orchestrator, crowd, facility, route, session, sos, geo
  data/                reference.py, temple.py, i18n.py
  middleware/          logging.py (structlog JSON + request ids)
alembic/               migrations
scripts/seed.py        idempotent reference-data seeding
tests/
```
