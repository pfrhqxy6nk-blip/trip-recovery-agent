# Trip Recovery Agent

Trip Recovery Agent is an autonomous recovery layer for trips that are already booked.
When one part of a journey changes, it calculates the downstream blast radius, repairs
safe dependencies, and asks the traveler only for consequential decisions.

Milestone 01 implements one real vertical slice:

```text
POST /simulate-disruption
  -> Pub/Sub topic trip-disruptions
  -> POST /internal/pubsub/disruptions (push subscription)
  -> atomic Firestore event claim + incident creation
  -> deterministic dependency impact calculation
  -> validated ADK/Gemini interpretation through Vertex AI
  -> persistent incident
```

## Local setup

Python 3.11 or newer is required.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

For real Google Cloud calls, authenticate with Application Default Credentials:

```bash
gcloud auth application-default login
```

Set `GEMINI_MODEL_ID` to the exact eligible model ID verified for the hackathon and
available in the configured Vertex AI location. It is deliberately not hardcoded.

Seed the demo trip and run the API:

```bash
PYTHONPATH=backend .venv/bin/python -m app.seed_demo
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload
```

The seeder is idempotent. It creates `demo-trip-001`, its four items, and three explicit
dependency edges.

For a local process without Google services, set `PUBSUB_TRANSPORT=local` and
`PROCESS_EVENTS_INLINE=true`. This keeps the publish/consume boundary but uses in-memory
adapters. Tests always inject fakes and never require credentials.

## API

```bash
curl -X POST http://localhost:8000/simulate-disruption \
  -H 'content-type: application/json' \
  -d '{
    "event_id":"demo-delay-001",
    "trip_id":"demo-trip-001",
    "type":"flight_delay",
    "flight":"LO351",
    "old_arrival":"2026-08-20T18:00:00Z",
    "new_arrival":"2026-08-20T19:45:00Z"
  }'
```

Production uses an authenticated Pub/Sub push subscription whose endpoint is:

```text
POST https://SERVICE_URL/internal/pubsub/disruptions
```

Cloud Run IAM must restrict invocation to the push subscription service account.

## Verification

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy
```

See [PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md), and
[MILESTONE_01.md](docs/MILESTONE_01.md).
