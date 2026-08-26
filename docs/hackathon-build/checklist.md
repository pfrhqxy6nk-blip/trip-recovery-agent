# Guided Build Checklist — Trip Recovery Agent

## Working agreement

- Build mode after explicit start: autonomous implementation with concise checkpoint reports.
- Handoff style: design is complete enough to execute milestone by milestone.
- Verification pauses: stop at security boundaries, external credentials, new infrastructure, and live deployment.
- Rollback point: create a local commit after each accepted milestone; never push without explicit approval.
- Scope protection: deterministic demo providers and the complete Telegram loop take priority over provider breadth.
- Wow moment: real disruption → proactive Telegram message → safe automatic work → €34/€20 approval → Firestore resume → verified `Trip recovered`.

## Milestone 0 — Freeze the baseline and contract ✓

**Build**

- Record the exact baseline commit/worktree and reconcile current uncommitted cloud-proof files.
- Update product/architecture docs from the approved PRD and spec.
- Add `.env.example` containing names only, never values.
- Document schema-v1 compatibility and the rule that no new milestone may use blind incident overwrites.

**Done when**

- Existing 12 tests, Ruff, and mypy still pass.
- `git diff --check` is clean and a secret scan finds no credentials.
- The baseline that produced the cloud proof is traceable.

**Pause**

- Report the resulting file set and ask before making the local milestone commit if the worktree includes unclear user changes.

## Milestone 1 — Domain, money, policy, and state machine ✓

**Build**

- Add integer-minor-unit `Money`, versioned traveler policy, recovery plan/action, approval, command, Telegram, and OAuth models.
- Add canonical serialization/hash rules and schema versioning.
- Define explicit incident/action transition tables.
- Encode immutable approval overrides and machine-readable policy reasons.

**Done when**

- Exhaustive policy truth-table tests cover AUTO/ASK, cumulative €20 spending, €34 approval, currency mismatch, ambiguity, irreversibility, penalty, and major changes.
- Hash tests prove dictionary/order stability and meaningful-change sensitivity.
- Illegal state transitions fail deterministically.
- Backward parsing of current incident documents is covered.

## Milestone 2 — Transactional repository and durable outbox ✓

**Build**

- Replace concurrent blind saves with compare-and-set repository primitives.
- Add event payload fingerprints, plan commits, action leases, semantic effect receipts, approval consumption, Telegram update claims, and deterministic outbox records.
- Make in-memory and Firestore adapters conform to one repository contract.
- Add an idempotent schema-v2 backfill/read-compatibility path.

**Done when**

- Two concurrent transition/action/approval attempts produce exactly one winner.
- A reused event ID with a different payload is rejected and auditable.
- Approval decision and continuation outbox are atomic.
- Firestore emulator/contract tests pass or any emulator limitation is explicitly documented with a substitute transaction test.

## Milestone 3 — Recovery planner and persistent deterministic providers ✓

**Build**

- Normalize and validate provider options; Gemini can rank/explain only admissible options.
- Create versioned canonical recovery plans and an ordered action DAG.
- Add Firestore-backed demo flight, transfer, hotel late-arrival, and calendar state.
- Implement the Warsaw → Munich → Lisbon option arriving 2h10 later for +€34.

**Done when**

- The 105-minute delay creates the expected impact, option, plan hash, and policy split.
- New facts or policy create a new plan and supersede old approval.
- Identical input does not create another plan.
- Demo-provider state survives application restart.

## Milestone 4 — Idempotent executor, verification, and recovery invariant ✓

**Build**

- Implement action claiming, dependency ordering, stable semantic effect keys, attempts, retry classification, and verification rereads.
- Handle provider-success/response-lost by checking provider state before retry.
- Implement the deterministic post-action trip conflict check.
- Prevent final success unless every required invariant holds.

**Done when**

- Crashes before and after provider mutation cause no duplicate effect.
- Provider 2xx plus mismatched reread does not verify.
- A plan revision with the same desired effect does not repeat it.
- `RECOVERED` is impossible with a pending approval, unresolved action, verification failure, or itinerary conflict.

## Milestone 5 — Durable recovery workflow ✓

**Build**

- Add small idempotent workflow commands for start, continue, approval resume, retry, expiry, and replan.
- Orchestrate plan → notification intent → auto-actions → wait → approved-actions → verify → completion entirely from Firestore.
- Add retry scheduling, needs-attention, cancellation, and supersession branches.
- Ensure final notification retries do not repeat provider actions.

**Done when**

- A deterministic local E2E test completes the canonical scenario.
- Restart at every phase boundary resumes at the correct cursor.
- Duplicate/out-of-order commands are no-ops or safe continuation.
- A new disruption while waiting invalidates old authority and replans.

## Milestone 6 — Telegram onboarding and approval edge ✓

**Build**

- Add `/start`, resumable seven-step onboarding, `/settings`, and policy versioning.
- Add webhook secret verification, payload limits, update dedupe, user/chat ownership, and rate limits.
- Add proactive awareness message, message editing, details, approve, find-another, stop, resume, and failure views.
- Use hashed opaque callback tokens and fast callback acknowledgement.

**Done when**

- Forged secret, malformed update, stale/expired/duplicate/cross-user callbacks mutate nothing.
- Repeated `/start` is idempotent and onboarding survives restart.
- `Show details` is read-only.
- Exactly one concurrent approval click creates one continuation.
- Telegram text accurately distinguishes verified, pending, skipped, and unresolved actions.

## Milestone 7 — Real Telegram plus secure service split

**Local status (2026-08-17):** adapter hardening, role-enforced route loading, delivery
alerts/redaction, and deployment/IAM preflight are complete with mocked/local tests. The
real-bot controlled verification remains behind the approval gate below.

**Build**

- Add the real Telegram Bot API gateway with retry-safe send/edit/answer behavior.
- Configure role-based edge/worker route loading locally.
- Prepare deployment/IAM manifests or scripts for public minimal edge and private worker.
- Add structured logging/redaction and operational delivery alerts.

**Done when**

- Real bot onboarding and approval work in a controlled environment.
- The worker exposes no public unauthenticated recovery endpoints.
- Telegram secret, bot token, callback tokens, chat data, and itinerary PII do not appear in logs.
- No cloud deployment occurs until explicitly approved.

**Approval gate**

- Ask before creating/changing Cloud Run, Pub/Sub, IAM, Secret Manager resources, setting a webhook, or exposing a URL.

## Milestone 7a — User-managed Gemini connection (BYOK)

**Local status (2026-08-17):** implementation and fake-adapter tests are complete. Live
Secret Manager storage, the public HTTPS page, and a real user key remain behind the
approval gate below.

**Build**

- Add `Connect Gemini` and `Disconnect Gemini` Telegram views with the official Google AI Studio key link.
- Create a short-lived, single-use connection handoff bound to the Telegram user/chat; accept the key only through the public HTTPS edge, never through Telegram.
- Store the value only in Secret Manager; persist only the metadata, masked fingerprint, and connection state in Firestore.
- Add an explicit per-traveler AI provider selector: user-managed Gemini key or system Vertex demo connection.
- Validate the connection with a minimal request and never silently fall back from a user key to the system key.

**Done when**

- Keys, authorization headers, and key-shaped strings are absent from Firestore, logs, Telegram responses, exceptions, prompts, and test fixtures.
- Expired, replayed, cross-user, and cross-chat connection handoffs are rejected without secret storage.
- A user can see connected/invalid/disconnected state and disconnect cleanly.
- A BYOK user request is executed only with that user's selected connection; a failed key produces a truthful actionable status.

**Approval gate**

- Ask before creating real Secret Manager secrets, exposing a public connection page, or accepting a real user API key.

## Milestone 7b — Demo-first Telegram experience ✓

**Local status (2026-08-17):** implemented and verified locally. Live deployment remains
behind the existing Cloud Run approval gate.

**Build**

- Put a safe 60-second recovery demo before onboarding and Gemini connection.
- Run the real deterministic impact, policy, recovery, approval, and verification path in
  an isolated per-traveler demo trip.
- Use one-message Telegram cards for trip state, causality, the €34/€20 decision, details,
  completion, replay, and lifecycle explanation.
- Keep demo provider state namespaced and label every demo claim truthfully.

**Done when**

- A new Telegram user can complete the demo without a Gemini key or real booking.
- The demo produces three verified safe actions, one approval-bound flight action, and a
  verified final recovery invariant.
- Details preserve the approval controls; duplicate/cross-user interactions are safe.
- Full tests, Ruff, mypy, and diff checks pass.

**Approval gate**

- Ask before deploying the updated worker or changing the live bot experience.

## Milestone 7c — Trip expenses and disruption ledger

**Local status (2026-08-17):** persistent ledger, verified recovery-cost capture, owner
scoping, Firestore/memory adapters, and the deterministic receipt demo are complete.
Real Telegram photo download and Gemini receipt extraction remain open.

**Build**

- Add integer-money expense records linked to trip, traveler, incident, source, category,
  receipt metadata, and deterministic deduplication keys.
- Automatically record verified paid recovery actions without treating bookkeeping as
  spending authority.
- Add Telegram expense capture, trip/disruption totals, and a demo receipt epilogue.
- Keep receipt extraction confidence explicit; low-confidence values require confirmation.

**Done when**

- Duplicate Telegram updates or workflow retries create one expense.
- The canonical +€34 recovery and €27.40 taxi demo produce a €61.40 disruption total.
- Money is never stored as float and cross-traveler expense access is rejected.
- Receipt images/API keys are never placed in Firestore logs or model prompts accidentally.

## Milestone 7d — Documents, readiness, and schedule guardian

**Local status (2026-08-17):** document metadata, deterministic missing-document checks,
connection-buffer assessment, owner scoping, Firestore/memory adapters, and demo readiness
cards are complete. Gmail/import adapters and live check-in/terminal sources remain open.

**Build**

- Add trip-document metadata, item linkage, presence state, and missing-document checks.
- Add deterministic pre-departure readiness checks for documents, check-in, terminal,
  transfer, timing, and unresolved tasks.
- Add schedule feasibility and unused-booking findings derived from the trip graph.
- Render one concise Telegram attention card instead of reminder spam.

**Done when**

- Readiness separates ready, missing, unknown, and needs-attention facts truthfully.
- A changed arrival deterministically exposes the impossible reservation and stale transfer.
- Document metadata is owner-scoped and document contents are not exposed in logs.

## Milestone 7e — Refunds, deposits, and trip closure

**Local status (2026-08-17):** persistent open financial items, exact settlement checks,
closure invariants, owner scoping, Firestore/memory adapters, and demo closeout are complete.
Live refund/deposit provider polling and evidence export remain open.

**Build**

- Add open financial items for refunds, deposits, and reimbursements with expected amount,
  due time, evidence, follow-up state, and settlement receipt.
- Add trip closure rules that keep a trip open while bookings, expenses, refunds, deposits,
  or claims remain unresolved.
- Add a post-trip Telegram closeout summary and exportable evidence model.

**Done when**

- Trip closure is impossible while a required financial or booking item remains open.
- Duplicate settlement events are idempotent and amount/currency mismatches need attention.
- The agent prepares follow-up evidence but never auto-submits a legal claim.

## Milestone 7f — Future-agent demo direction ✓

**Local status (2026-08-17):** implemented and verified locally. The live Telegram bot
still runs the previous worker revision until a separate deployment approval.

**Build**

- Turn the demo features into one directed five-stage story: watch, resolve, verify,
  remember costs, and close the financial tail.
- Add an agent map, readable cause chain, explicit authority boundary, and persistent
  recovery receipt without inventing real-provider claims.
- Add opt-in Telegram HTML hierarchy while preserving plain-text views elsewhere.
- Make the primary button at each stage advance the story; keep exploration and setup as
  secondary actions.

**Done when**

- A first-time user can understand what the agent observed, what it did automatically,
  why it interrupted them, and how it proved completion without opening a dashboard.
- The proof view is available only for an owned, recovered demo with every action verified.
- The final story truthfully reports one interruption, four verified effects, €61.40 in
  disruption expenses, and two financial items before closure.
- Full tests, Ruff, strict mypy, and diff checks pass.

**Approval gate**

- Ask before deploying the updated worker or changing the live Telegram experience.

## Milestone 7g — First-user sources and monitoring foundation

**Build**

- Add a persistent Telegram manual-trip draft: one or more flights, optional hotel, explicit
  confirmation, ownership/chat binding, and time-zone-aware validation.
- Add source/coverage/freshness and monitoring-snapshot contracts. A changed, authorized
  observation must emit a deduplicated existing disruption event.
- Start with deterministic source adapters and tests; add one real flight-status source only
  after its credential, usage budget, and polling infrastructure are separately approved.

**Done when**

- A first user can add and confirm an owned itinerary without sending a booking reference or
  raw document.
- Duplicate/replayed/cross-user intake cannot alter another traveler's draft or trip.
- Stale or failed monitoring is displayed truthfully and cannot be interpreted as on-time.
- The source-to-disruption path is tested without external network access.

**Approval gate**

- Ask before accepting real documents, enabling Gmail OAuth, adding Cloud Scheduler, storing
  a real status-provider credential, or starting external polling.

## Milestone 8 — Google Calendar OAuth and verified action

**Status: contract and mocked verification complete; live consent/configuration remains gated.**

**Build**

- Implement Authorization Code + PKCE, single-use hashed state, exact redirect binding, minimal scope, Secret Manager token storage, refresh, disconnect, and revoke.
- Implement Calendar event update/upsert with semantic effect marker and reread verification.
- Mark Calendar truthfully as `NOT_CONNECTED` or optional-skipped when authorization is absent.

**Done when**

- OAuth CSRF/state replay and cross-user binding tests pass.
- Tokens never enter Firestore, logs, Telegram, or Gemini prompts.
- Duplicate update produces one calendar effect.
- Reread proves expected start/end/timezone/version before the checklist claims success.

**Approval gate**

- Ask before opening OAuth consent, storing a real refresh token, or changing Google Cloud OAuth configuration.

## Milestone 9 — Real cloud end-to-end proof

**Build**

- With authorization, add workflow command topic/subscription, authenticated private push, dead-letter handling, and outbox delivery/sweeper.
- Deploy immutable edge and worker revisions tied to a commit/image digest.
- Run real event → Pub/Sub → worker → ADK/Gemini → Firestore → Telegram → approval → resume → provider verification.
- Publish duplicate event/command/callback and deliberately interrupt one phase.

**Done when**

- One correlated trace proves the complete canonical flow.
- Duplicates and restart create no repeat action or Gemini call where prohibited.
- Firestore records and provider rereads agree with traveler messages.
- IAM proves public access is limited to the edge.

**Approval gate**

- This milestone changes cloud state and must not start without explicit permission.

## Milestone 10 — Duffel and Gmail stretch adapters

**Build**

- Add Duffel sandbox options/quote/change/order verification behind existing ports.
- Add Gmail OAuth only if needed; automatic work creates a draft, while send remains approval-required.
- Preserve deterministic demo adapters as the recorded-demo fallback.

**Done when**

- Changed/expired Duffel quote invalidates approval and never executes stale authority.
- Duffel order/segment state is independently reread.
- Gmail draft/send is reread by provider ID and never mislabeled reversible.
- No card data or broad inbox scope is introduced.

**Scope gate**

- Skip this milestone if Milestones 0–9 or demo reliability are incomplete.

## Milestone 11 — Hardening, evidence, and demo rehearsal

**Local status (2026-08-23):** canonical deterministic E2E runner and CI quality gates are
implemented; three consecutive local rehearsals pass. Immutable cloud provenance and container
scanning remain required before submission.

**Build**

- Add CI for tests, Ruff, mypy, dependency audit, secret scan, and container scan.
- Remove mypy exclusions for workflow-critical adapters.
- Build a reproducible E2E evidence script and redact all artifacts.
- Rehearse success, duplicate-click, restart-resume, and verification-failure stories.
- Draft architecture diagram, demo script, README, and Devpost text locally.

**Done when**

- Full suite passes from a clean environment.
- Three consecutive canonical rehearsals succeed without manual state repair.
- Failure rehearsal never emits false success.
- Evidence points to immutable source/image provenance.
- Submission materials contain no secret or private traveler data.

**Publication gate**

- GitHub push, making revisions public, video upload, and Devpost submission require separate explicit approval.

## Definition of product-complete for the hackathon

The agent is ready to present when a real Telegram traveler can onboard once, receive a proactive disruption message, observe safe authorized actions, approve only the +€34/€20 boundary, and receive a verified recovery checklist after a persistent cloud resume. The demo must remain correct under duplicate delivery, duplicate click, and one deliberate restart.
