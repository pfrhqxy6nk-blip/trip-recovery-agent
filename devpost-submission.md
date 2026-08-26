# Title

Trip Recovery Agent

This is a local submission draft only. Use [SUBMISSION_GATE.md](docs/SUBMISSION_GATE.md)
for the final live acceptance, security, and owner-supplied Devpost fields. Nothing is
published from this repository by the checklist.

## One-line Summary

A Telegram-first autonomous travel agent that watches an itinerary, repairs safe downstream consequences, and interrupts the traveler only for consequential decisions.

## Problem

Travel disruptions are not isolated alerts. A delayed flight can make a connection, airport transfer, hotel arrival, and calendar entry wrong at the same time. Travelers usually discover each consequence manually, across several apps, while under time pressure.

## Solution

Trip Recovery Agent turns an existing booking into a persistent trip graph. A traveler can start in Telegram, forward a ticket or booking artifact, and receive a structured itinerary. The agent validates external signals, computes downstream impact, performs policy-allowed reversible work, and asks for approval only when money, ambiguity, penalties, or irreversible changes are involved. The workflow resumes from durable state after approval and verifies the resulting state before reporting recovery.

## Why This Matters

The product promise is: always informed, rarely interrupted. It removes coordination work without hiding authority boundaries or inventing certainty. The canonical demo shows a 105-minute Warsaw → Munich disruption, three safe updates, a +€34 flight decision against a €20 automatic limit, persistent approval resume, and a verified recovery receipt.

## How We Used AI

- Google ADK and Gemini through Vertex AI provide structured interpretation and ranking/explanation where language or public-source context is needed.
- Gemini Vision/Document handling is used for forwarded PDFs, Booking/Airbnb confirmations, screenshots, and Apple Wallet `.pkpass` files. The extractor returns PNR, flight/date/time/terminal details, hotel stays, and connection facts; the offline fallback refuses to invent missing explicit times and never creates a synthetic flight for a hotel-only booking.
- Google Search-grounded Trip Watch accepts a signal only with source metadata. Official airline/airport evidence is distinguished from a lead that still needs review.
- Compensation assessment covers EU261, UK261, and US DOT paths. Claim drafts remain review-only and require explicit airline-fault evidence before an incident can be claim-ready.
- Deterministic code owns policy, impact, state transitions, money, idempotency, and verification; Gemini cannot authorize a prohibited external action.

## How We Used Codex

Codex was used to turn the product direction into the PRD, architecture, state-machine contracts, and milestone plan; implement the FastAPI/ADK/Firestore/Pub/Sub/Telegram slices; generate and refine the landing experience; add multimodal intake and compensation safeguards; run pytest, Ruff, mypy, landing build/Sites tests, diff checks, and security-oriented scans; and prepare the Google Cloud proof and this local Devpost draft. The build notes record the decisions and the boundaries between demo adapters and real providers.

## Key Features

- Telegram `/start`, resumable onboarding, `/settings`, policy versioning, approval, details, stop/resume, and duplicate-safe callbacks.
- Chat-first trip planning: write a destination, nights, origin/date, budget, and interests in
  plain language; the agent asks for only missing fields and returns grounded options before a
  real booking is forwarded.
- Multimodal itinerary intake for PDF, Booking/Airbnb confirmation, screenshot, and `.pkpass` metadata, with ownership, explicit confirmation, hotel-only support, and a 12 MiB/magic-byte upload guard.
- Persistent trip graph and deterministic impact engine for flights, connections, hotels, transfers, activities, weather, and calendar dependencies.
- Background/event-driven workflow: Pub/Sub → authenticated Cloud Run worker → Firestore state → Telegram delivery.
- Autonomous safe actions with cumulative spending limits, immutable approvals, action leases, retries, effect receipts, rereads, and a strict `RECOVERED` invariant.
- EU261/UK261/DOT compensation assessment and an owner-bound Telegram button that opens an
  evidence-linked, reviewable claim draft after recovery (never auto-submitted).
- Trip expense ledger, readiness checks, financial-tail closure, and a future-agent Telegram demo.
- White-background editorial landing page with the Telegram recovery story and live bot CTA.

## Architecture

```text
Telegram Bot API / Pub/Sub
          │
          ▼
Public Cloud Run edge (secret + route validation)
          │ authenticated IAM invocation
          ▼
Private Cloud Run worker (FastAPI + Google ADK)
          ├── deterministic impact / policy / recovery engine
          ├── Gemini on Vertex AI + optional Search grounding
          ├── multimodal itinerary extractor
          ├── compensation/evidence service
          ├── Firestore transactional state + outbox
          └── Pub/Sub and Telegram delivery adapters
```

Google Cloud proof is recorded in [docs/CLOUD_PROOF.md](docs/CLOUD_PROOF.md), including the deployed worker and edge revisions, immutable image digest, authenticated edge contract, and duplicate-event behavior. A hardened Cloud Function remains available only as rollback. The architecture artifact is [docs/architecture-diagram.pdf](docs/architecture-diagram.pdf).

## Testing Instructions

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check backend/app backend/tests cloud_function
.venv/bin/mypy backend/app backend/tests
git diff --check

cd landing
npm install
npm run build
npm run test:sites
```

Run the deterministic showcase with:

```bash
PYTHONPATH=backend .venv/bin/python -m app.demo_recovery
```

For the live pilot, send `/start` to [@tripagentai_bot](https://t.me/tripagentai_bot), choose
`Start my trip` or `Plan a trip`, then write a natural request or forward a supported itinerary
artifact. There is no public demo path. The first live smoke still requires a human Telegram
message; no synthetic user update is used as proof.

## Public Demo Link

`TODO: add a hosted landing URL if one is published for judging.`

Live Telegram bot: [@tripagentai_bot](https://t.me/tripagentai_bot)

## Public Repository Link

`TODO: confirm whether the GitHub repository should remain private. If private, share it with testing@devpost.com and cloudhackathons@google.com as required by the event.`

Repository currently configured locally: `https://github.com/pfrhqxy6nk-blip/trip-recovery-agent`

## Demo Video

`TODO: record and add a ~4-minute video URL.`

Suggested sequence: problem (20s) → start in Telegram and plan “Paris, 6 nights, €600” (35s) →
forward PDF/screenshot and show extracted itinerary (40s) → show signal validation and impact
graph (35s) → show Telegram proactive message and three safe actions (45s) → approve +€34
against €20 (35s) → restart/persistent resume and verified receipt (40s) → Cloud
Run/Firestore proof and architecture (30s) → limitations and value proposition (20s).

## Screenshot Shot List

1. Telegram `/start` onboarding with autonomy/spending policy controls.
2. Multimodal intake: forwarded document/screenshot becoming a structured trip graph.
3. Proactive disruption message with source, impact, actions already handled, and approval boundary.
4. Post-approval `Trip recovered` receipt with verified actions and no unresolved conflicts.
5. Google Cloud proof: Cloud Run revision, Pub/Sub flow, Firestore state, and the landing/Telegram experience.

## Submission Readiness Notes

- Official event requirements checked on 2026-08-23: Gemini 3.5+; a Google Agent Framework; Google Cloud; repository URL; architecture diagram; required demo video; required fields for category, project start date, repo, reproducible testing, Google SDKs/services, and AI models.
- Live Devpost judging criteria favor operational autonomy (40%), architectural discipline and stack (30%), and a clear production-ready demo (30%); the project is positioned in the **Taskmaster** category.
- The live event window is currently open through 2026-09-01 00:00 UTC; the exact owner-entered date, submitter fields, architecture upload, and video remain intentionally unfilled here.
- Best-fit category: **Taskmaster**. The agent is event-driven, asynchronous, and completes a multi-step travel recovery workflow without hand-holding.
- Local verification is green: full backend tests (including the real webhook planning path), Ruff, strict mypy, landing production build, Sites tests, browser smoke, npm audit, and `git diff --check`.
- Cloud deployment proof exists for the edge/worker split; current worker revision and digest are recorded in `docs/CLOUD_PROOF.md`.
- Devpost project is still a local draft in this repository. Nothing was submitted from this workflow.

## Known Limitations

- Real airline/transfer/hotel mutations are not enabled; deterministic providers prove the workflow and later Duffel/Calendar/Gmail adapters remain scoped work.
- Multimodal extraction is strongest when Gemini Vision is configured. The deterministic fallback rejects media without explicit times instead of fabricating a trip; hotel-only, MIME-sniffing, archive-expansion, and request-size hardening are deployed and regression-tested.
- Compensation is a reviewable draft, not legal advice or automatic claim submission. A
  recovered real incident exposes the draft only to the owning Telegram user; airline-fault
  attribution and source evidence are required.
- A real Telegram `/start` smoke and final visual browser review still need to be performed by the participant before the video is recorded.
- A sealed Codex Security report is available for the scanned snapshot (scan ID
  `5e56645d-fdd2-4d5c-9df9-41b750283967`). It reported two medium findings; both are fixed in
  the current worktree; the fixes are pending the next owner-approved worker/edge rollout. TAC enrollment and final billing
  budget configuration remain owner-controlled submission gates.

## TODO Official Form Fields

- Submitter Type: `TODO — Individuals / Team of individuals / Organization`
- Submitter country of residence: `TODO`
- Category: `Taskmaster`
- Organization name: `N/A unless submitting on behalf of an incorporated organization`
- Project start date (MM-DD-YY): `TODO — verify from the actual project history`
- Code repository URL: `https://github.com/pfrhqxy6nk-blip/trip-recovery-agent`
- Reproducible Testing instructions in README: `Yes`
- Hosted project URL: `TODO`
- Testing instructions for judges: `Use the testing section above; live bot smoke requires /start`
- Google SDK: `Agent Development Kit (ADK); Google GenAI/Vertex AI integration`
- Google Cloud services: `Cloud Run; Firestore; Pub/Sub`
- Architecture diagram: `docs/architecture-diagram.pdf`
- Google AI models: `Gemini 3.5 Flash or newer, exact model ID supplied by deployment configuration`
- Demo video URL: `TODO`
- Optional bonus content/social links: `TODO / intentionally not added`
