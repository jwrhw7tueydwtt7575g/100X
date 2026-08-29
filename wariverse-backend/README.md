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
| GET | `/api/crowd/{zone_id}` | Crowd level, wait time and quieter alternatives |
| GET | `/api/facilities/nearby` | Facilities near a coordinate, nearest first |
| GET | `/api/routes/guidance` | Step-by-step walking guidance along the palkhi route |
| GET | `/api/temple/info` | Darshan types, aarti schedule, rules, live queue |
| POST | `/api/lost-found` | File a lost person / lost item report |
| GET | `/api/lost-found/{ref_id}` | Look up a report by reference id |
| POST | `/api/sos/trigger` | Panic button — dispatch help to a coordinate |
| POST | `/api/auth/otp/send` | Send a login OTP |
| POST | `/api/auth/otp/verify` | Verify the OTP, receive a JWT |

Every endpoint accepts `?language=mr|hi|en|kn|te`; unsupported values fall back
to `DEFAULT_LANGUAGE`. Errors use one envelope:

```json
{"error": {"code": "http_404", "message": "unknown zone_id: foo", "details": null},
 "request_id": "9f2c…"}
```

### Examples

```bash
curl -s localhost:8000/api/crowd/vitthal_temple?language=mr

curl -s "localhost:8000/api/facilities/nearby?lat=17.6786&lon=75.33&facility_type=water&radius_m=2000"

curl -s localhost:8000/api/conversation/message \
  -H 'content-type: application/json' \
  -d '{"text":"जवळ पाणी कुठे मिळेल?","location":{"lat":17.6786,"lon":75.33}}'
```

---

## Design notes

**Degrade, never disappear.** Startup does not abort when Postgres or Redis is
unreachable; the API keeps serving safety information from cache or from the
bundled reference dataset, and `/health/ready` reports the real state for the
orchestrator to act on. The one deliberate exception is `POST /api/lost-found`,
which returns 503 rather than accepting a report it cannot store.

**Crowd reads are layered.** Redis (30 s TTL) → latest Postgres snapshot →
deterministic estimate. The `source` field is always one of `live`, `cache` or
`estimated`, so the app can show an estimate as an estimate.

**Safety wording is not generated.** SOS prompts, crowd warnings and helpline
numbers come from `app/data/i18n.py`. The LLM only rephrases facts that the
domain services computed and passed to it as context; the prompt forbids
inventing timings, distances or numbers. With no API key, a timeout or an API
error, the rule-based responder answers instead and the response is labelled
`source: "rules"`.

**Geo without PostGIS.** The Wari corridor fits in a ~250 km box, so a bounding
box on a btree `(lat, lon)` index plus a haversine in Python is accurate to
metres on stock Postgres.

**OTPs are never stored or logged in plaintext** — only an HMAC of `phone:otp`
keyed with `JWT_SECRET`, in Redis, under `OTP_TTL_SECONDS`. `_deliver_otp()` in
`app/routers/auth.py` is the single seam for an SMS provider; outside production
the code is echoed as `debug_otp` so the app can be exercised end to end.

**SOS is unauthenticated by design.** A pilgrim in trouble who never registered
must still be able to call for help. A bearer token, when present, attributes
the event so responders can call back.

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
