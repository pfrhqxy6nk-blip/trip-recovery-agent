# Title

Trip Watch

## One-line Summary

A Telegram-first autonomous travel agent that turns bookings into protected trips: it understands travel documents, watches for disruption, repairs safe consequences, and asks only when a decision needs the traveler.

## Problem

Travel disruption is a dependency problem, not an alert problem. A delayed inbound flight can invalidate a connection, luggage transfer, airport pickup, hotel arrival, and calendar at once. Today, the traveler has to detect each consequence across multiple apps while stressed and moving through an airport.

## Solution

Trip Watch creates a durable trip graph from a traveler’s booking. The traveler can either plan a new trip in normal language or forward a PDF ticket, booking confirmation, screenshot, email, or `.pkpass` file. Gemini extracts and interprets bounded facts; deterministic code validates the itinerary, calculates the blast radius of an event, applies the traveler’s autonomy policy, and drives a persistent recovery workflow.

The agent acts first only where it is safe and authorized. It sends a proactive Telegram update, handles reversible downstream work, and returns a single approval only when a choice involves money, a penalty, ambiguity, or an irreversible change. After approval, it resumes from Firestore, verifies the effects, and sends a recovery receipt.

## Why This Matters

Trip Watch removes the coordination burden that makes small disruptions expensive and stressful. It does not hide decision-making behind an LLM: the traveler stays in control of consequential actions while the agent handles the time-sensitive work in the background.

## How We Used AI

- **Gemini 3.5 Flash+ through Vertex AI** powers multimodal itinerary extraction from forwarded documents and grounded interpretation of bounded public travel signals.
- **Google ADK** provides the agent boundary around Gemini. Model output is treated as untrusted and validated before it can affect the workflow.
- Gemini explains and ranks only validated facts. It cannot invent a fare, approve a payment, decide a connection is feasible, or execute a provider action.
- The agent uses Gemini/Google Search grounding for planning and Trip Watch when grounding evidence is available. When it is unavailable, the user sees an explicitly labelled estimate rather than false real-time inventory.
- The project’s differentiator is the separation between probabilistic reasoning and deterministic authority: code owns money, policy, temporal feasibility, idempotency, state transitions, and verification.

## How We Used Codex

Codex was used as a development collaborator for product planning, implementation, code review, iterative Telegram QA, tests, landing refinement, architecture documentation, and reproducibility checks. It helped turn a traveler journey into a durable Google Cloud workflow while keeping the product’s claims and demo boundaries explicit. AI coding assistance was used during the contest period; the project story, architecture, implementation choices, and submitted work are the entrant’s original product work.

## Key Features

- Natural-language planning with three comparable transport + hotel options, source links, budget totals, and clear `Search-grounded` versus `Estimate` labels.
- Multimodal booking intake for PDFs, booking emails, screenshots, and `.pkpass`, with safe deterministic fallback and no fabricated itinerary data.
- Persistent dependency graph for flights, connections, transfers, hotel arrival, weather, and calendar context.
- Background Trip Watch with scoped watchpoints, source validation, durable notification delivery, and proactive Telegram updates.
- Deterministic blast-radius analysis and a Visa & Baggage Guardian that escalates ambiguity rather than guessing clearance.
- Autonomy policies, spending ceilings, one-time owner-bound approvals, idempotent provider effects, durable resume, reread verification, and recovery receipts.
- EU261 / UK261 / DOT compensation assessment with an evidence-linked, review-only claim draft — never automatically sent.
- Data-minimizing design: deletion command, bounded document parsing, MIME checks, PII minimization in model prompts, Secret Manager-backed optional credentials, and a private Cloud Run worker.

## Architecture

```text
Telegram / public travel signals / controlled demo event
                         │
                         ▼
   Cloud Run edge (validates Telegram request and routes commands)
                         │ IAM-authenticated invocation
                         ▼
Cloud Run worker (FastAPI + Google ADK + Gemini / Vertex AI)
   ├─ deterministic impact, policy, recovery, verification
   ├─ multimodal extraction and grounded interpretation
   ├─ Firestore: trips, graph, policy, incidents, outbox, receipts
   └─ Pub/Sub: disruption events and durable workflow resume
                         │
                         ▼
                 proactive Telegram result
```

Attach [docs/architecture-diagram.pdf](docs/architecture-diagram.pdf) in the Devpost form. [docs/CLOUD_PROOF.md](docs/CLOUD_PROOF.md) records verified Cloud Run, Pub/Sub, Firestore, and Vertex evidence.

## Testing Instructions

### Public product links

- Landing: https://trip-watch.vercel.app/
- Telegram bot: https://t.me/tripagentai_bot
- Repository: https://github.com/pfrhqxy6nk-blip/trip-recovery-agent

### Judge flow

1. Open the Telegram bot and send `/start`.
2. Complete the brief autonomy setup.
3. Forward `demo/fixtures/warsaw-munich-lisbon-booking.pdf` from the repository. It is synthetic and explicitly marked **DEMO ONLY / NOT VALID FOR TRAVEL**.
4. Confirm the extracted draft and select **Save trip**.
5. Send `/demo`, select **Simulate verified +195 min delay**, then select **Approve +€34**.
6. Verify that the chat displays the recovery receipt and a review-only €250 EU261 claim draft.

The fixture is safe to share and contains no real booking, payment, or personal information. The demo is controlled: it proves persistent workflow, policy, idempotency, and verification without claiming that a synthetic reservation was changed in the real world.

### Local reproduction

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
scripts/run_submission_gate.sh
PYTHONPATH=backend .venv/bin/python -m app.demo_recovery
```

For the landing page:

```bash
cd landing
npm install
npm run build
npm run test:sites
```

## Public Demo Link

https://trip-watch.vercel.app/

## Public Repository Link

https://github.com/pfrhqxy6nk-blip/trip-recovery-agent

## Demo Video

`TODO — add the public YouTube or Vimeo URL before submission.`

Suggested 4-minute sequence:

1. **0:00–0:20 — Problem:** one delay destroys a chain of travel commitments.
2. **0:20–0:45 — Value:** Trip Watch is autonomous but policy-bounded, not a chatbot.
3. **0:45–1:20 — Intake:** forward the demo PDF in Telegram, review and save the trip.
4. **1:20–2:30 — Background agent:** trigger the +195 minute delay; show impact, Visa & Baggage Guardian, safe actions, and one €34 approval.
5. **2:30–3:05 — Durable result:** approve, then show the verified receipt and review-only claim draft.
6. **3:05–3:35 — Google Cloud proof:** Cloud Run / Firestore / Pub/Sub / Vertex evidence and the architecture diagram.
7. **3:35–4:00 — Honest close:** what is deployed now and what remains provider-gated.

The video must be public and in English or include English subtitles.

## Screenshot Shot List

1. Minimal Telegram onboarding with the autonomy / spending policy.
2. Synthetic PDF becoming a structured itinerary draft.
3. A proactive disruption message showing connection impact and safe actions.
4. The single €34 approval boundary followed by `RECOVERY VERIFIED`.
5. Google Cloud proof and the architecture diagram.

## Submission Readiness Notes

- **Recommended category:** `Taskmaster` — it is a multi-step, event-driven workflow that takes action autonomously.
- **Required Google stack:** Gemini 3.5+ via Vertex AI, Google ADK, Cloud Run, Firestore, and Pub/Sub.
- **Required evidence still to attach:** public video, uploaded architecture PDF, actual project start date, actual submitter type/country, and final live smoke confirmation.
- **Required disclosure:** confirm that the submitted project was created during the official submission period and disclose any pre-existing or third-party code/assets truthfully.
- **Do not overclaim:** real ticket purchase, payment capture, Calendar writes, Gmail drafts/sends, and live external-provider mutations are not part of the judge demo unless separately enabled and verified.

## Known Limitations

- Planning results are not bookings; they become verified itineraries only after a traveler forwards booking evidence.
- Google Search grounding can be unavailable because of quota or shared capacity. The product labels fallback results as estimates.
- Real airline rebooking, payments, hotel/transfer mutations, Calendar writes, Gmail drafts, and third-party live monitoring require separate credentials, user consent, provider contracts, and reread verification. They remain disabled by default.
- Compensation is an evidence-based draft for review, not legal advice and not an automatic claim submission.
- Visa and baggage screening are conservative heuristics; unknown cases require human confirmation.

## TODO Official Form Fields

- Submitter Type: `TODO — choose actual Individual / Team of individuals / Organization`
- Submitter country of residence: `TODO — enter actual country`
- Category: `Taskmaster`
- Organization name: `N/A unless entering as an incorporated organization`
- Project start date (MM-DD-YY): `TODO — enter actual date; do not guess`
- Code repository URL: `https://github.com/pfrhqxy6nk-blip/trip-recovery-agent`
- Reproducible Testing instructions in README: `Yes`
- Hosted project URL: `https://trip-watch.vercel.app/`
- Google SDK: `Agent Development Kit (ADK)`
- Google Cloud services: `Cloud Run; Firestore; Pub/Sub`
- Architecture diagram: upload `docs/architecture-diagram.pdf`
- Google AI model: `Gemini 3.5 Flash+ via Vertex AI` (enter the deployed model ID truthfully)
- Demo video: `TODO — public YouTube/Vimeo URL`
- Optional bonus content / social post: `TODO or leave blank`
