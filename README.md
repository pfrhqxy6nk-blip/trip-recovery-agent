# Trip Recovery Agent

Trip Recovery Agent is a Telegram-first travel agent with two explicit paths: plan a new
trip from a natural-language brief, or protect an itinerary that is already booked. For
planning, Gemini + Google Search returns concrete transport and hotel candidates with
prices, times, conditions and source links; confirmation creates a private `PLANNED` record
without pretending anything was purchased. Forwarding the real PDF, email, screenshot or
`.pkpass` then replaces the estimate with verified booking data and activates monitoring.
When one part of a booked journey changes, the agent calculates the downstream blast radius,
repairs safe dependencies, and asks the traveler only for consequential decisions.

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

The baseline production slice has been verified in Google Cloud: authenticated Pub/Sub push invokes
Cloud Run, Google ADK calls Gemini 3.5 Flash through Vertex AI, and the deterministic impact
plus validated interpretation are persisted in Firestore. See
[CLOUD_PROOF.md](docs/CLOUD_PROOF.md) for the exact revision, event, trace, and idempotency
evidence.

The hackathon architecture artifact is available as [PNG](docs/architecture-diagram.png)
and [PDF](docs/architecture-diagram.pdf).

## Local setup

Python 3.11 or newer is required.

## Local recovery showcase

Run the deterministic end-to-end recovery scenario:

```bash
.venv/bin/python -m app.demo_recovery
```

It demonstrates a 105-minute Warsaw → Munich delay, three verified automatic updates,
the €34 versus €20 approval boundary, a persistent workflow resume, and a verified
`RECOVERED` result. It uses local deterministic provider adapters; it does not send a
Telegram message or modify an external booking.

The broader controlled-pilot loop now also includes resumable Telegram onboarding, a real
Bot API gateway, proactive durable notifications, current-plan approval/details/stop
callbacks, restart-safe action execution, owned manual-trip intake, focused Google
Search-grounded Trip Watch, safe conversational Telegram messages, and a BYOK Gemini
handoff backed by Secret Manager. The judge deployment additionally exposes a bounded,
read-only Vertex AI demo path using project credits (20 shared requests/day, 5 requests/user/day,
256 output tokens/request, one worker instance); it fails truthfully when the daily guardrail or Google
billing/quota is exhausted. See [USER_JOURNEY.md](docs/USER_JOURNEY.md). The private
worker and Scheduler are deployed. The latest local hardening build is validated by the
submission gate but must be rolled out to Cloud Run before claiming those changes are live.
Real booking mutations still require the later provider adapters described below.

The watch loop is autonomous: Cloud Scheduler claims due watchpoints, validates cited
signals, persists a deduplicated disruption, publishes the recovery event, executes only
policy-safe actions, and resumes durable commands after approval. An opt-in Amadeus
production adapter can provide authoritative flight-status observations; it is disabled
until credentials are injected through Secret Manager. Search grounding remains the source
for public airport, weather, hotel, transfer, and activity signals. Non-recovery signals are
also delivered proactively to Telegram as durable, source-linked `WATCH_SIGNAL` notices;
they inform the traveler without silently authorizing a booking change.

### Planning a trip versus protecting a booking

Send a message such as `I want to go to Paris for 6 nights, budget €600, from Kyiv`.
The planner returns three compact, comparable cards (flight/train/bus + hotel, total estimate,
dates and times, cancellation conditions and tappable HTTPS sources). Live cards are labelled
`Search-grounded` only when Vertex AI returns Google Search grounding evidence. If Search or
quota is unavailable, the same shape is returned as an explicit `estimate` and is never shown
as current inventory. Choosing a route immediately persists a `PLANNED` trip; it does not book,
charge or start disruption recovery. Forwarding the actual booking in the chat is the separate
handoff that creates the confirmed itinerary and its watchpoints.

The live Telegram bot endpoint is wired through the public Cloud Run edge to the private worker;
the hardened Cloud Function adapter remains available only as a rollback path.
Send `/start` to [@tripagentai_bot](https://t.me/tripagentai_bot) to exercise the first-user
flow. Forward a PDF ticket, Booking/Airbnb confirmation, screenshot, or `.pkpass`; Gemini
Vision/Document extraction can return flights, hotel stays, PNRs, terminals and connections
when the project model is configured. A hotel-only confirmation is valid and does not create a
synthetic flight. The bounded deterministic fallback accepts explicit ISO-8601 times (or
`pass.json` metadata) and refuses to invent a booking. Media is capped at 12 MiB and its MIME
type is checked from magic bytes before it reaches the model. EU261/UK261/DOT claim drafts are
review-only and require explicit airline-fault evidence.

To reset a test account, send `/delete_my_data` in Telegram. The command removes that
traveler's trips, watchpoints, workflow history, drafts, expenses and OAuth metadata from
Firestore and destroys the associated Secret Manager credential versions when configured.
It is scoped to the authenticated Telegram user; it cannot delete another traveler's data.
Cloud Logging is an operational audit system and follows the retention policy of the Google
Cloud project. See [PRIVACY_AND_RETENTION.md](docs/PRIVACY_AND_RETENTION.md).

### Judge-ready beta fixtures

The repository includes a safe, synthetic input pack in
[demo/fixtures](demo/fixtures/README.md): a visual PDF booking confirmation, a forwarded-booking
email text file, an unsigned `.pkpass` fixture, and a synthetic airport delay signal. Every file is
labelled **DEMO ONLY / NOT VALID FOR TRAVEL** and contains no real reservation or payment data.
Regenerate the pack with:

```bash
.venv/bin/python scripts/build_beta_fixtures.py
```

Use the PDF to exercise the multimodal path; its explicit text layer also works in the safe
offline fallback. Use the email text or `.pkpass` fixture to demonstrate deterministic parsing
without inventing missing booking details. The full judge sequence is documented in
[docs/SHOWCASE_01.md](docs/SHOWCASE_01.md) and the fixture-specific handoff is in
[demo/fixtures/README.md](demo/fixtures/README.md).

### Portfolio / resume framing

Describe this as a **hackathon-grade autonomous travel recovery prototype**, not as a
live airline booking platform. The strongest, accurate resume claims are:

- Built a Telegram-first, event-driven agent with Vertex AI/Gemini multimodal intake and
  Search-grounded trip monitoring.
- Implemented deterministic blast-radius analysis, durable Firestore/Pub/Sub workflows,
  policy-gated recovery, idempotent action receipts, and audit-friendly safety limits.
- Added Visa & Baggage Guardian heuristics, EU261/UK261/DOT claim drafts, a judge simulator,
  and a full automated quality gate.

Real airline rebooking and payment capture remain future provider integrations. Calendar and
Gmail have production-oriented OAuth adapters, but stay disabled by default until the project
owner configures consent, Secret Manager IAM, and both provider reread checks. Gmail uses
`gmail.compose` solely to create a draft; this implementation has no inbox-reading or send
endpoint. The current demo deliberately uses
safe adapters and synthetic fixtures.

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
APP_ROLE=worker PYTHONPATH=backend .venv/bin/uvicorn app.runtime:app --reload
```

The seeder is idempotent. It creates `demo-trip-001`, its four items, and three explicit
dependency edges.

For a local process without Google services, set `PUBSUB_TRANSPORT=local` and
`PROCESS_EVENTS_INLINE=true`. This keeps the publish/consume boundary but uses in-memory
adapters. To use the demo trigger, also set `ENABLE_SIMULATOR=true` and a 16+ byte
`SIMULATOR_SECRET`. Tests always inject fakes and never require credentials.

For a BYOK-enabled HTTPS pilot, set `ENABLE_BYOK_CONNECTIONS=true` and
`CONNECTION_BASE_URL` to the deployed `/connections/gemini` page. A Telegram deployment
also requires a bot token, 16+ byte webhook secret, and 32+ byte approval callback signing
key. Keep all values in Secret Manager or an untracked local `.env`.

Run the complete local submission gate before a release:

```bash
scripts/run_submission_gate.sh
```

For the owner-controlled, read-only Telegram and Cloud Run contract in the same run, export
the local secrets first and set `RUN_LIVE_CHECK=1`. This never sends a Telegram message or
changes infrastructure.

## API

```bash
curl -X POST http://localhost:8000/simulate-disruption \
  -H 'content-type: application/json' \
  -H 'X-Trip-Agent-Simulator-Secret: replace-with-your-local-secret' \
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
