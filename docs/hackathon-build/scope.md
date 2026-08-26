# Project Scope

## Project Name Candidates

- Trip Recovery Agent (selected)
- Trip Guardian
- Recovery Copilot

## One-Line Summary

Trip Recovery Agent is a Telegram-first autonomous recovery layer for already-booked trips that detects disruption impact, performs policy-approved safe actions, requests approval only for consequential decisions, resumes durably, and verifies that the trip is recovered.

## Target User

A traveler with a multi-part booked journey who does not want to monitor airline changes or manually coordinate every downstream consequence while in transit.

The hackathon MVP supports one Telegram user and one canonical trip at a time while keeping the persistence model compatible with multiple users and trips.

## Problem

Existing travel alerts tell travelers that something changed but leave them to determine whether a connection is still feasible, find an alternative, update transfers and calendars, notify a hotel, judge costs, and verify every change. During disruption, that is exactly when the traveler has the least attention available.

The product promise is: the traveler is always informed but interrupted only when a decision genuinely requires them.

## Time Budget

- External deadline window: approximately two weeks from this scope decision.
- Scope ruler: target a demo-complete MVP in seven focused build days and reserve the remaining window for real-cloud integration, failure handling, evidence, and demo rehearsal.
- This is a planning assumption and can be tightened if the participant provides a smaller hour budget.

## Core Workflow

1. The traveler opens the Telegram bot and completes policy onboarding.
2. A normalized disruption event arrives through the simulator or provider adapter.
3. The existing pipeline atomically claims the event, calculates deterministic downstream impact, and stores a validated Gemini interpretation.
4. A recovery planner creates a versioned plan containing proposed actions, costs, risk classes, verification rules, and stable idempotency keys.
5. A deterministic policy engine splits actions into automatically allowed and approval-required sets.
6. The agent proactively sends a concise Telegram impact message and executes allowed safe actions.
7. If approval is required, the incident persists in `WAITING_APPROVAL` with a plan hash, quote, currency, and expiry.
8. A Telegram callback validates the user, incident, plan version/hash, quote, expiry, and current status.
9. The workflow resumes from Firestore, executes the remaining actions exactly once, and verifies every provider by rereading external state.
10. The agent sends `Trip recovered` only when all required invariants pass; otherwise it reports the unresolved item and a safe next step.

## What We Are Building

### MVP spine

- Telegram webhook with secret-token verification, `/start`, onboarding callbacks, recovery approval callbacks, and proactive messages.
- Persistent Telegram identity and an autonomy policy covering notifications, calendar changes, service messages, reversible actions, spending limit, and mandatory approval classes.
- Versioned `RecoveryPlan`, `PlannedAction`, `PolicyDecision`, `ApprovalRequest`, and execution/verification result models.
- Deterministic policy evaluation; Gemini can explain or rank options but cannot grant authority.
- Recovery workflow phases: plan, notify, execute allowed actions, wait, resume, execute approved actions, verify, recover/fail.
- Stable action idempotency keys and compare-and-set/transactional transitions for duplicate callbacks and retries.
- Provider ports for flight recovery, calendar, service messaging, and Telegram.
- Deterministic demo providers so the complete scenario always works locally and in a recorded demo.
- Real Telegram integration as the primary interface.
- At least one real Google Workspace action, preferably Calendar first; Gmail follows if time permits.
- Structured execution logs and automated tests for policy, approvals, resume, idempotency, provider failures, and verification.

### Stretch integrations

- Duffel sandbox ingestion and recovery option adapter.
- Gmail hotel late-arrival notification.
- Google OAuth connection flow for Calendar and Gmail.
- Full deployment pipeline using Cloud Build, Artifact Registry, Cloud Run, Secret Manager, IAM, Firestore, Pub/Sub, Vertex AI, and Cloud Logging.

## What We Are Not Building

- A traveler-facing web dashboard or mobile application. It weakens the Telegram-first story and consumes demo time.
- A generic conversational travel chatbot, itinerary generator, destination recommender, or booking marketplace.
- Support for arbitrary airlines, hotels, transfers, currencies, and every disruption type.
- Production payment processing or custody of a traveler's card.
- Guaranteed live airline rebooking where a sandbox provider cannot deterministically support the demo action.
- Automatic execution of irreversible, ambiguous, penalty-bearing, or materially different itinerary changes.
- Complex multi-user administration, team travel, customer-support tooling, or analytics dashboards.
- Broad Gmail inbox access; only minimum scopes needed for an explicitly approved action.

## Inspiration And References

- Flight-status products: immediate, calm clarity about what changed and why it matters.
- A skilled human travel agent: coordinates downstream consequences instead of merely reporting the problem.
- Transactional workflow systems: every action is versioned, idempotent, resumable, auditable, and verified.

The product borrows these qualities without becoming another flight tracker, chat window, or operations dashboard.

## Demo Path

1. Show completed Telegram autonomy settings, including a EUR 20 spending limit.
2. Trigger the canonical Warsaw -> Munich -> Lisbon event: LO351 is delayed by 105 minutes.
3. Show deterministic proof that the Munich connection is infeasible and transfer, hotel arrival, and calendar are affected.
4. The agent proposes a replacement arriving 2h10 later for EUR 34.
5. It automatically handles all policy-allowed actions and reports them in Telegram.
6. Because EUR 34 exceeds the EUR 20 limit, Telegram shows `Approve recovery` and `Show details`.
7. Click approval once, then deliberately click again to demonstrate stale/duplicate protection.
8. The persisted workflow resumes, executes the flight recovery adapter once, verifies flight, transfer, hotel, and calendar state, and sends the final recovered checklist.
9. Show the correlated Cloud Logging trace and Firestore incident/action records as architecture evidence, not as the product UI.

## Submission Story

Most travel assistants answer questions. Trip Recovery Agent reacts to real events, reasons over validated trip state, acts within the traveler's standing policy, pauses only at a genuine authority boundary, survives restarts, and proves that every external consequence was repaired.

The strongest judging evidence is the contrast between probabilistic reasoning and deterministic control: Gemini interprets, ranks, explains, and replans; code owns time, cost, authority, state transitions, idempotency, and verification.

## Scope Decisions Requiring No Further Blocking Question

- Use one polished recovery scenario before adding breadth.
- Use Telegram as the only traveler-facing UI.
- Treat Firestore as the authoritative workflow store.
- Implement provider interfaces with deterministic demo adapters before live provider adapters.
- Prefer a real Calendar action over attempting every external integration at once.
- Never publish, push, deploy, or submit without explicit participant approval.
