# Build Notes

## Onboarding

- The participant defined the product as a real autonomous travel agent living inside Telegram, not a dashboard and not a conventional chatbot.
- Core promise: the traveler is always informed but interrupted only when a decision genuinely requires them.
- Existing Milestone 01 implementation and architecture documents must be incorporated rather than replaced.
- Privacy constraint: planning and implementation remain local. No push, deployment, or Devpost publication without explicit approval.
- Active shaping: the participant redirected the process from submission preparation back to idea development and implementation planning.
- The participant confirmed the target technology set: Telegram Bot API, Cloud Run, FastAPI, Google ADK, Gemini through Vertex AI, a deterministic impact engine, Firestore, Pub/Sub, Duffel, Google Calendar, Gmail, OAuth 2.0, Secret Manager, IAM/service accounts, Cloud Logging, Pydantic, pytest, Ruff, mypy, Docker, Artifact Registry, and Cloud Build.
- Scope discipline: these technologies form the target architecture, but implementation will be phased. The persistent Telegram recovery loop is the MVP spine; Duffel, Calendar, and Gmail remain provider adapters added after the core state machine and policy gates work end to end.

## Scope

- The participant asked Codex to proceed from the already supplied requirements and decide what code should be generated, so no additional brain-dump round was required.
- Selected one canonical disruption-to-recovery demo rather than broad provider coverage.
- Assumed a seven-focused-build-day MVP budget inside the remaining deadline window; this remains adjustable.
- Selected inspiration qualities: flight-alert clarity, human-agent coordination, and transactional workflow auditability.
- Cut from MVP: traveler web UI, generic chat, arbitrary trip ingestion, production payments, broad provider coverage, and guaranteed live airline rebooking.
- The first new code milestone is the persistent Telegram policy/approval/resume spine. Real provider adapters follow stable ports and deterministic demo adapters.
- Deepening rounds taken: 0. The participant explicitly requested implementation-oriented progress from the detailed requirements already supplied.

## Cloud proof milestone

- Deployed the existing backend to a private Cloud Run service in `europe-west3` using a dedicated least-privilege runtime service account.
- Converted the existing `trip-disruptions-sub` subscription from pull to authenticated push with a dedicated service-scoped invoker identity.
- Verified a real Pub/Sub -> Cloud Run -> ADK/Gemini 3.5 Flash -> Firestore execution using event `cloud-e2e-20260816-001`.
- Verified cloud idempotency by publishing the same event ID again: HTTP 200, unchanged Firestore attempt count, and no second Gemini call.
- Corrected deployment context by moving the Dockerfile from `backend/` to the repository root.
- Evidence is recorded in `docs/CLOUD_PROOF.md`.
- No GitHub push or Devpost publication was performed.

## Build — autonomous continuation and Calendar contract

- Approval callbacks now persist a `RESUME_AFTER_APPROVAL` workflow command in the durable
  outbox; the authenticated Pub/Sub command consumer claims and resumes it after a process
  boundary. The local e2e proves signal → impact → safe actions → Telegram approval → command
  delivery → verified recovery without inline execution.
- Added a security-first Google Calendar contract: Authorization Code + PKCE, single-use hashed
  state bound to Telegram user/chat and the exact HTTPS redirect URI, minimal Calendar Events
  scope, Secret Manager-only refresh-token storage, disconnect, and deterministic mocked tests.
- Added an idempotent Calendar action provider that writes a private semantic effect marker and
  rereads the event before marking it verified. `HybridActionProvider` routes only a connected
  Calendar to this provider; missing/revoked authorization produces terminal
  `calendar_not_connected`, never a false “calendar updated”. Recovery can select a provider per
  traveler while preserving deterministic demo providers for the recorded hackathon scenario.
- The live OAuth consent screen, client credentials, and Cloud rollout remain explicit production
  gates. Until those are configured, the canonical demo is honest and fully autonomous inside
  its persistent deterministic provider boundary.

## PRD, specification, and implementation plan

- Three read-only agent audits covered product/Telegram behavior, transactional architecture/security, and QA/release delivery. Their findings were reconciled into one PRD and one technical specification.
- Corrected the next-milestone dependency: transactional compare-and-set workflow primitives must precede Telegram callbacks and external action execution.
- Selected two Cloud Run trust boundaries for the target architecture: a minimal public Telegram/OAuth edge and the existing private authenticated worker. No cloud changes were made during planning.
- Added the production Telegram compensation handoff: after a recovered non-demo incident,
  the owner receives a callback-bound “Review compensation” control with escaped claim text,
  evidence links/timestamps, and an explicit review-only/no-auto-send boundary.
- Selected integer minor-unit money, cumulative per-incident EUR authority, versioned plans/policies/approvals, semantic effect keys, persistent provider state, verification rereads, and a strict final recovery invariant.
- Resolved the service-message reversibility issue: a persistent demo late-arrival record can be automatic; Gmail draft creation may be reversible; Gmail send is irreversible and requires approval.
- Selected an eleven-milestone implementation sequence plus baseline milestone, with deterministic adapters and the persistent Telegram loop ahead of Duffel/Gmail breadth.
- Prepared copy/paste execution prompts containing scope, files, constraints, tests, definitions of done, prohibited actions, and explicit gates for credentials, cloud mutations, deployment, push, and publication.
- Future working preference: autonomous milestone execution with concise checkpoints, local rollback commits after accepted milestones, and mandatory pauses at external/security gates.
- No runtime code, cloud resource, GitHub repository, Telegram webhook, OAuth configuration, or Devpost submission was changed during this planning pass.

## Build — Milestone 0 complete

- Recorded the current source provenance: Milestone 01 is commit `bd9ca78`; the verified Cloud Run proof also depends on the existing uncommitted root Dockerfile relocation, ignore files, README note, and `docs/CLOUD_PROOF.md`.
- Added `.env.example` with local-development defaults and blank placeholders for future secrets. No credential values were introduced.
- Updated product and architecture documentation with the correct automatic-spending rule, schema-v1 compatibility boundary, and prohibition on using blind `save_incident()` writes in new concurrent paths.
- No runtime behavior, cloud resource, webhook, OAuth configuration, GitHub state, deployment, commit, push, or publication was changed.

## Build — Milestone 1 complete

- Added immutable integer-minor-unit `Money`, versioned `AutonomyPolicy`, provider-normalized policy candidates, deterministic decisions/reason codes, recovery option/plan/action models, plan status, and schema-v2-oriented incident states.
- Added canonical JSON/SHA-256 hashing and semantic effect keys. Ordered action lists remain semantic; mapping/set order does not affect a fingerprint.
- Added a deterministic policy engine: mandatory risk reasons are evaluated before user preferences and spending authority; automatic spend is cumulative per incident and supports the canonical €34 vs €20 decision.
- Added an explicit incident state-transition table. Legacy Milestone 01 statuses remain compatible while future workflow phases are represented separately.
- Added 15 focused tests for money, hashing, policy truth-table cases, and state transitions. Full suite: 27 passed; Ruff and mypy pass; `git diff --check` is clean.
- No Firestore transaction behavior, external provider, Telegram/OAuth API, cloud resource, deployment, commit, push, or publication was changed.

## Build — Milestone 2 complete

- Added repository contract primitives for event payload fingerprints, expected-version incident transitions, plan commits, action leases, semantic effect receipts, approval consumption, Telegram update claims, and deterministic outbox records.
- Implemented the contract in both memory and Firestore adapters. Existing Milestone 01 paths remain compatible; new concurrent paths have explicit APIs rather than relying on blind `save_incident()` writes.
- Added a pure, idempotent schema-v1 → schema-v2 incident backfill helper. It performs no I/O; applying changes to real Firestore remains an explicit authorization gate.
- Added in-memory contract tests for reused event-ID conflicts, concurrent CAS transitions, concurrent action claim/effect completion, and atomic approval/outbox consumption. A Firestore emulator is not configured in this local workspace, so the shared in-memory contract suite is the current deterministic substitute; Firestore-specific emulator verification remains required before cloud workflow deployment.
- Full suite: 32 passed; Ruff and mypy pass; `git diff --check` is clean. No external API, cloud resource, deployment, commit, push, or publication was changed.

## Build — Milestone 3 complete

- Added the canonical recovery planner for the deterministic Warsaw → Munich → Lisbon 105-minute disruption. It produces a versioned +€34 option arriving at 23:15, a canonical plan hash, stable semantic effect keys, and an action dependency graph.
- The policy split is demonstrated in code: transfer, hotel late-arrival record, and calendar are auto-approved under the selected policy; the replacement flight is approval-required because €34 exceeds the remaining €20 incident authority.
- Added a persistent demo-provider port backed by repository state rather than a provider-local in-memory map. The Firestore adapter writes `demoProviderState`; the local adapter supplies deterministic tests.
- Added planner and provider tests. Full suite: 34 passed; Ruff and mypy pass; `git diff --check` is clean. No external API, cloud resource, deployment, commit, push, or publication was changed.

## Showcase 01 — first local end-to-end recovery test

- Added the bounded `RecoveryWorkflow`: plan commit → policy-allowed action execution → persistent approval request → atomic approval/outbox consumption → approved action → verification guard → `RECOVERED`.
- Added idempotent action execution and provider reread verification. The final recovery guard rejects pending approvals, unverified actions, and verification failures.
- Added `backend/tests/test_recovery_e2e.py`, covering the full canonical sequence and a duplicate approval click. It proves exactly four effect receipts and one continuation outbox record.
- Added the runnable terminal demo `PYTHONPATH=backend .venv/bin/python -m app.demo_recovery` and `docs/SHOWCASE_01.md` for a live walkthrough.
- Verification: demo command reaches `Trip recovered: RECOVERED`; full suite 38 passed; Ruff, mypy, `git diff --check`, and secret scan pass.
- This is deliberately a local deterministic showcase, not a claim of Telegram or live-provider completion. Milestones 4 and 5 remain open until their full restart/fault-injection and durable-cloud criteria are met.

## Build — Telegram onboarding edge (partial Milestone 6)

- Added a local `/telegram/webhook` edge contract with constant-time secret-header validation, update deduplication, and user/chat binding.
- Added `/start` and `/settings` behavior through a resumable seven-step button flow. The profile persists Calendar, service-message, reversible-change, spending, and policy-version choices.
- Added API tests for full activation, forged-secret rejection, duplicate update handling, and cross-user callback rejection.
- Added `docs/USER_JOURNEY.md` as the traveler-facing installation and disruption path.
- Full suite: 41 passed; Ruff and mypy pass; `git diff --check` is clean. This is a local structured Telegram edge, not a live bot/webhook registration; approval, details, cancellation, and real Bot API delivery remain open Milestone 6 work.

## Product decision — user-managed Gemini access (BYOK)

- The participant chose the first user-facing cost model: each traveler connects their own Gemini API key rather than the product silently paying for Gemini requests.
- The bot will provide the official Google AI Studio key page and a one-time secure HTTPS connection handoff. Keys are never accepted in Telegram, stored in Firestore, written to logs, or shown back to the traveler.
- This applies to Gemini-model usage only. Cloud Run, Firestore, observability, and travel-provider costs remain separate product operating costs. The existing Vertex path stays as an explicit internal/demo connection, not a hidden BYOK fallback.

## Controlled first-user readiness pass — 2026-08-17

- Added the real Telegram Bot API gateway and wired send/edit/callback acknowledgement into
  the webhook. Proactive awareness, approval, details, final, and stop views now use the
  persistent recovery workflow.
- Added owned pilot-trip intake and an isolated controlled scenario. Onboarding copy no
  longer claims that a real trip is monitored before one is added.
- Added durable outbound Telegram intent/receipt records. Already-receipted messages are
  deduplicated; unknown delivery outcomes are not blindly repeated and do not unlock
  provider actions.
- Added restart-stable HMAC approval callbacks while storing only callback hashes. Approval
  consumption revalidates current trip incident, plan/policy/option/amount/currency/expiry,
  user/chat, and update identity. A newer incident invalidates older pending authority.
- Added lease reclamation, provider-state reconciliation after response loss, dependency
  ordering, semantic-effect reuse across plan revisions, and strict recovery verification.
- Removed blind incident writes from impact analysis. Deterministic impact is CAS-committed;
  interpretation plus event completion is one transaction.
- Added the BYOK handoff, secure static connection page, Secret Manager adapter, minimal
  credential validation, metadata-only Firestore records, disconnect, and explicit
  per-traveler routing with no Vertex fallback for a user-managed identity.
- Secured `/simulate-disruption`: disabled by default and protected by a separate secret
  when explicitly enabled.
- Local verification: 85 tests pass; Ruff and strict mypy pass; `git diff --check` is clean;
  the credential-pattern scan is clean. Docker is not installed in the current workspace,
  so a clean container build remains an external pre-deploy check.
- No deployment, webhook registration, real secret creation, real API-key acceptance,
  cloud mutation, commit, push, or publication was performed.

## Active shaping — lifecycle and demo-first scope

- The participant expanded the product from recovery-only toward a full trip lifecycle:
  trip building, documents, readiness, live monitoring, schedule guarding, recovery,
  expenses, refunds/deposits, and closure.
- Product priority remains coherent rather than feature-count driven: the trip graph is the
  backbone and Telegram exposes only current state, meaningful findings, decisions, and
  verified receipts.
- Added Milestones 7b–7e to preserve the selected build order. The first completed slice is
  a demo-first Telegram experience that runs before onboarding and requires neither Gemini
  nor a real booking.
- The demo executes the deterministic impact/policy/recovery engine in isolated traveler
  state and labels all provider actions as demo effects. Local verification: 90 tests pass;
  Ruff and strict mypy pass; touched files are formatted and `git diff --check` is clean.
- No deployment, commit, push, or publication was performed for this UX change.

## Build checkpoint — demo lifecycle foundation

- Added a persistent trip expense ledger using integer minor units. Verified paid recovery
  actions create deterministic expense records; duplicate workflow/callback processing does
  not duplicate money. The demo adds a €27.40 taxi receipt to the verified €34 flight cost
  and reports €61.40 in disruption expenses.
- Added owner-scoped document metadata and deterministic readiness. The demo identifies a
  missing transfer voucher and a tight 55-minute connection, then becomes READY after the
  demo voucher is persisted. No visa or legal-entry inference was introduced.
- Added refunds/deposits as open financial items and a closure invariant. The demo trip
  remains blocked with a €150 deposit and €70 refund open, rejects amount mismatch, and
  reaches CLOSED only after exact settlement.
- Added matching memory and Firestore repository methods for expenses, documents, financial
  items, settlement, and trip closure status.
- Verification: 96 tests pass; Ruff and strict mypy pass; all touched files are formatted;
  `git diff --check` is clean.
- Remaining product work is explicitly truthful: live receipt image extraction, Gmail trip
  import, provider monitoring, real refund polling, export, Calendar OAuth, and Duffel stay
  open. No new revision was deployed and nothing was pushed or published.

## Active shaping — future-agent Telegram demo

- The participant asked for a demo and UX that feels like a product from the future. The
  product-design decision was to express intelligence through continuity, causality,
  authority, and proof rather than add a dashboard or decorative feature volume.
- Reframed the existing demo into one five-stage journey: agent watching, impact resolved,
  recovery verified, cost memory, and financial-tail closure. Every primary button advances
  the story; the agent map and readiness scan remain optional exploration.
- Added Telegram HTML hierarchy as an opt-in view property, a detailed deterministic cause
  chain, an exact approval trace, and an owned recovery-receipt view that refuses to render
  unless the incident is recovered and all actions are verified.
- Improved testability by making the application clock injectable; this removed a
  pre-existing real-time dependency from the public BYOK edge test without changing BYOK
  behavior.
- Verification: 97 tests pass; Ruff and strict mypy pass; `git diff --check` is clean.
  Nothing was deployed, pushed, published, or submitted.

## Build — Milestone 4 complete

- Reconciled the checklist against the existing executor: expired action leases, provider
  reread after response loss, semantic-effect reuse, dependency ordering, and the strict
  recovery invariant were already implemented and covered by fault-injection tests.
- Added explicit provider failure classification. Retryable failures transition to
  `FAILED_RETRYABLE` with a persisted `retry_after`; terminal failures transition to
  `FAILED_TERMINAL` and cannot be claimed again. Exceptions with an unknown outcome keep
  the lease so a replacement worker rereads provider state before any mutation.
- Added immutable, sanitized `ActionAttempt` records with attempt number, worker, outcome,
  retry class, provider reference, timestamps, and bounded error code. Memory and Firestore
  adapters share the same contract; no raw provider response is persisted.
- Added tests proving a retry cannot happen before its due time, a retryable failure succeeds
  exactly once later, a terminal failure is never retried, and response-loss recovery records
  a reconciled unknown-outcome attempt without a duplicate external effect.
- Verification: 99 tests pass; Ruff and strict mypy pass; `git diff --check` is clean. No
  cloud resource, deployment, secret, commit, push, publication, or submission changed.

## Build — Milestone 5 complete

- Added persisted, idempotent commands for start, continuation, approval resume, action
  retry, approval expiry, and replanning. Each command has a deterministic payload hash,
  durable claim/lease, completion state, and duplicate/out-of-order protection.
- Added a durable outbox dispatcher with publish receipts and due-time scheduling. Approval
  now creates resume and expiry commands; retryable action failures schedule a retry, while
  terminal or verification failures enter `NEEDS_ATTENTION` without unsafe repetition.
- Recovery can resume at every phase boundary. A crash after an external mutation reconciles
  provider state before retrying, a final Telegram delivery retry does not repeat provider
  actions, and concurrent command workers produce one effect.
- Expired authority is atomically invalidated and replanned. The replacement plan receives a
  new version and the prior plan is explicitly marked `SUPERSEDED`; newer disruption tests
  prove stale approval authority cannot be reused.
- Verification: 107 tests pass; Ruff passes across the backend, tests, and Cloud Function;
  strict mypy passes across 96 backend/test source files; `git diff --check` and the secret
  scan are clean. The Cloud Function keeps its own deployment dependencies and is not part
  of the backend virtual environment's strict-type gate.
- No cloud resource, deployment, secret, commit, push, publication, or submission changed.

## Build — Milestone 6 complete

- Hardened the Telegram webhook before business handling: constant-time secret validation,
  a 64 KiB raw-body limit, strict supported-update validation, payload-collision detection,
  durable per-user/message-kind rate windows, and update deduplication.
- Callback queries are acknowledged before repository or workflow work. Real recovery
  approval now atomically records authority and a continuation outbox item, then returns a
  truthful queued state; the safe isolated demo remains intentionally inline.
- Completed the recovery controls: read-only details, `Find another option`, two-step stop,
  and `Resume recovery`. Replan requests are ownership-bound, callback-hash-bound,
  transactionally deduplicated, supersede old authority, and create a fresh plan version.
- Telegram recovery cards now render verified, pending-approval, skipped, failed, and
  unresolved states from persisted actions rather than claiming a fixed success list.
  Expired detail views remove authority controls.
- Added fixed and custom (€1–€500) spending limits. Policy activation now atomically writes
  an immutable versioned policy document; settings edits remain a draft until activation,
  so they cannot silently change authority used by an active workflow.
- Added hostile payload, oversized body, update collision, rate-limit, immediate-ack,
  custom-limit, immutable-policy, stop/resume, concurrent-replan, expiry, and truthful-card
  tests. Verification: 115 tests pass; Ruff and strict mypy pass across 96 backend/test
  source files; `git diff --check` and the credential-pattern scan are clean.
- No real token was used and no webhook, cloud resource, deployment, commit, push,
  publication, or submission changed.

## Build — Milestone 7 local preflight complete

- Enforced runtime roles. The container now starts through `app.runtime`; the safe default
  is `worker`, which loads internal disruption and private connection-completion routes but
  no Telegram webhook or public connection page. `edge` loads only health, Telegram
  webhook, and Gemini connection proxy routes; `all` is explicitly local-only.
- Hardened the existing real Telegram Bot API adapter contract. Mocked tests cover send,
  edit, callback acknowledgement, 429 retry-after, 5xx, connection/read timeout,
  edit-not-modified, blocked bot, and malformed successful responses with unknown outcome.
- Split terminal delivery from uncertain delivery. A blocked bot is persisted as `BLOCKED`
  with a sanitized failure code and is not blindly retried; unknown outcomes remain
  `UNKNOWN`. Structured ERROR events support a Cloud Monitoring log alert without exposing
  chat IDs or message contents.
- Added centralized structured-log redaction for Telegram API token URLs, bearer values,
  API-key shapes, opaque callback tokens, Telegram identities, and email addresses. Only a
  bounded allow-list of operational correlation fields is emitted as structured metadata.
- Added an inert-by-default Cloud Run/IAM preflight under `infra/cloudrun/`. It documents
  exact environment/secret names, the public-edge/private-worker boundary, existing and
  proposed service identities, and exits without mutation unless `APPLY=true` is supplied.
- Verification: 123 tests pass; Ruff and strict mypy pass across 99 backend/test source
  files; deployment shell syntax, `git diff --check`, and the credential-pattern scan pass.
- Milestone 7 is intentionally not checked complete yet: real bot onboarding/approval in a
  controlled environment still requires explicit permission to use the configured token,
  deploy edge/worker revisions, modify IAM, and set the webhook. None of those actions was
  performed in this pass.

## Active shaping — first-user data sources

- The participant correctly identified that a Telegram recovery demo is not sufficient: a
  real traveler must be able to add their own trip and understand what the agent actually
  monitors.
- The chosen order is safe manual intake first, then deterministic monitoring contracts,
  then one real flight-status source. Documents, Gmail, GTFS-Realtime, hotels, and
  activities are deliberately separate source types with honest coverage labels rather than
  one misleading promise of universal real-time monitoring.
## 2026-08-17 — Monitoring contract foundation

- Added owner-bound persistent monitoring subscriptions and normalized observation snapshots.
- Manual itinerary entry creates only `Schedule stored` coverage; it never claims live status.
- A deterministic fixture can emit a deduplicated existing disruption event only after exact
  source, trip item, owner, and scheduled-arrival checks. Real flight polling remains behind
  the explicit credential/budget/infrastructure approval gate.

## 2026-08-17 — Amadeus flight-status adapter

- Added a production-only OAuth client for Amadeus On-Demand Flight Status, with token reuse,
  one 401 refresh, strict response parsing, and no secret logging.
- The test endpoint remains rejected because Amadeus documents it as non-real-time data.
- Polling is intentionally not scheduled until a production client ID/secret is supplied and a
  cross-instance request budget is implemented with the deployment configuration.

## 2026-08-22 — Conversational Trip Watch rollout

- Added safe natural-language Telegram messages for coverage, weather monitoring, trip status,
  adding a trip, Gemini connection, and settings. Text is read-only navigation/explanation;
  it cannot authorize a booking, payment, or recovery action.
- Added owner-scoped trip listing so status responses cannot expose another traveler’s trip.
- Updated the first-user journey and truthful cloud boundary documentation.
- Verification: 72 source files type-check; full test suite, Ruff, and diff checks pass.
- Built and deployed private worker image `telegram-chat-20260822` as revision
  `trip-recovery-agent-00007-hks`; startup probe succeeded. The public edge and Telegram
  webhook were not changed.

## 2026-08-22 — Bounded shared-credit judge mode

- Added a read-only Vertex AI/Gemini explanation path for the hackathon judge deployment.
  Judges do not enter a Gemini key; known local help/status/demo flows remain deterministic,
  while open explanatory questions may use the project’s Vertex credits.
- Added one atomic Firestore quota bucket shared across all Telegram users: 20 calls per UTC
  day, 256 output tokens per call, and one Cloud Run worker instance. The prompt forbids
  bookings, payments, credential collection, and claims of external changes. Gemini output is
  escaped before Telegram HTML rendering.
- Any Vertex API, quota, or billing failure returns an explicit unavailable message and leaves
  the deterministic demo usable. This is a spend guardrail, not a replacement for a Google
  Cloud Billing budget alert owned by the project administrator.
- Deployed image `judge-vertex-20260822` as revision `trip-recovery-agent-00008-k9b` with
  `ENABLE_JUDGE_MODE=true`. No user key is exposed and no external booking mutation is enabled.

## 2026-08-23 — Owner-bound compensation review rollout

- Added a real-incident Telegram `Review compensation` button after recovery. The callback
  verifies Telegram user and chat ownership, renders source links/timestamps and the generated
  EU261/UK261/DOT draft, and explicitly keeps sending manual/review-only.
- Built image digest `sha256:5147d8d6ab81b78420f28ea443cd3780b478d855868ffe53b0422fd51aa55744`
  and deployed it as private Cloud Run revision `trip-recovery-agent-00013-wpp` with one
  max instance and the existing runtime service account/secrets.
- Smoke checks: unauthenticated worker `403`, edge health `200`, signed malformed Telegram
  contract `400 malformed Telegram update`. Telegram's historical webhook 404 is cleared only
  after the owner sends a fresh `/start` (no synthetic user update was sent).

## 2026-08-23 — Autonomous planning and shared-credit rollout

- Added persistent Telegram trip planning: a traveler can start from a destination, dates,
  budget and interests, receive three clearly labeled estimates, select one, and continue by
  forwarding real booking evidence. The planner never fabricates a booking or availability.
- Added explicit opt-in recommendation preferences, kept separate from monitoring and never
  used to send unsolicited travel ideas by default.
- Removed public demo affordances from onboarding and the main Telegram menu. The deterministic
  replay remains an internal judge harness only, so the first user is met by a real welcome and
  onboarding flow.
- Judge mode now uses one atomic Firestore `vertex-global` quota bucket across chat, planning
  and Search-grounded Trip Watch: 20 project calls/day, 256 output tokens/call, maxScale=1.
  Quota exhaustion or Vertex failure falls back to truthful deterministic estimates/status and
  does not claim live booking changes.
- Built image digest `sha256:9fa9856ee4605f63ef8e603f5f3b0f5515f9dd7ebc129724e49f6d4b00982fb7`
  (tag `autonomy-plan-20260823-v2`) and deployed private Cloud Run revision
  `trip-recovery-agent-00014-lfl`. Ready/ConfigurationsReady/RoutesReady are true; root
  unauthenticated invocation returns 403.
- Verification: Ruff, strict mypy, all backend tests, and `git diff --check` pass. No repository,
  landing page, webhook, or Devpost submission was published or changed by this rollout.

## 2026-08-23 — Durable Trip Watch delivery

- Grounded Search signals are now a durable outbox: Firestore records the accepted fact before
  Pub/Sub publication and stores `published_at` only after acknowledgement. A deterministic
  event fingerprint excludes delivery metadata, so a replay is idempotent across restarts.
- `/internal/watch/tick` flushes pending signals first, isolates per-watchpoint/provider failures,
  and returns `failed_watchpoints` for structured operational evidence instead of aborting the
  entire autonomous loop.
- Planning `Save this plan` now persists `planning_saved_at` with compare-and-set semantics.
- Verification: full backend pytest suite, Ruff, strict mypy, and diff checks pass.

## 2026-08-24 — Autonomous recovery truth boundary and live quote adapter

- The Cloud Run runtime now uses a judge-only deterministic provider. It can persist and
  verify the cinematic replay only when the immutable incident id starts with
  `telegram-demo:`; a real traveler incident receives `demo_recovery_disabled` and is never
  marked recovered by a fake provider effect.
- Added an optional Duffel v2 search adapter behind `ENABLE_DUFFEL_QUOTES`. It searches an
  expiring recovery offer and persists provider id, price, arrival, expiry and a snapshot hash.
  It is quote-only: no order, payment, confirmation, or card data is handled. Duffel order-change
  execution remains a separate gated adapter requiring a real Duffel order id and an approval.
- Added per-user and global Vertex limits for judge mode so a single public Telegram user cannot
  consume the shared project budget. The bounded fallback remains explicit when Vertex is
  unavailable or exhausted.
- Verification: Ruff, strict mypy, the Duffel mock contract and real-incident demo-gate tests
  pass. A production Duffel token is still required before live quote search can be enabled.
- Built image digest `sha256:4f72bd0590c2afc8324a1f1dd7fbd7774df2d6db032dea564d37ed80dfd1424f`
  (tag `autonomy-durable-watch-20260823-v3`) and deployed private Cloud Run revision
  `trip-recovery-agent-00015-7dc`; maxScale remains 1 and Scheduler remains enabled.

## 2026-08-23 — Source trust and autonomous-save hardening

- Grounded event identity now excludes all polling/delivery timestamps (`observed_at`,
  `source_updated_at`, and `published_at`), so a repeated unchanged search result cannot create
  a new recovery event merely because the watcher ran again.
- Official flight evidence must use HTTPS and match the airline domains attached to that
  watchpoint. Gemini's source label is advisory; an untrusted host is downgraded/rejected at the
  deterministic boundary and cannot authorize an autonomous change.
- Telegram `Save this plan` now registers and verifies watchpoints before clearing the planning
  draft. A worker restart or provider failure therefore leaves the draft retryable instead of
  producing a saved-looking trip that is not actually monitored.
- Regression coverage includes repeated-poll deduplication, untrusted official-source rejection,
  direct-tick acknowledgement of non-recovery facts, and the full Telegram planning path.
- Built image digest `sha256:35c7750dd557d5be4f836010d79dcd1b94b6a783c91859bbf3590d930e25c247`
  (tag `autonomy-source-trust-20260823-v4`) and deployed private Cloud Run revision
  `trip-recovery-agent-00016-s5f`; worker root remains 403, edge health is 200, and
  `trip-watch-tick` remains ENABLED every 30 minutes.
- Standard Codex Security scan completed with zero validated findings. Live IAM remains an
  operational follow-up rather than a source-code claim.

## 2026-08-23 — Event-driven approval resume

- Approval callbacks now commit a continuation command to a durable Firestore outbox rather
  than doing recovery work inline in the Telegram request.
- Added the authenticated `trip-workflow-commands` Pub/Sub topic/subscription and the
  `/internal/pubsub/commands` consumer. It resumes the persisted recovery plan, preserves
  command idempotency, verifies actions, and sends the final Telegram status.
- `/internal/watch/tick` sweeps pending continuation commands as a retry boundary, so a
  transient worker or Pub/Sub failure does not strand an approved recovery.
- Added a full local e2e proof: Search-grounded signal → Pub/Sub disruption → awareness and
  approval → Telegram callback → command Pub/Sub → verified recovery.
- Built image digest `sha256:31c812fdfe4218e566f1fa6bed24564c1c19583d6aef77a02bfabb68e3a46a3a`
  (tag `autonomy-command-resume-20260823-v5`) and deployed private Cloud Run revision
  `trip-recovery-agent-00017-hkl`. The command topic and authenticated push subscription are
  ACTIVE; worker remains maxScale=1 and Scheduler remains ENABLED.

## 2026-08-23 — Calendar autonomy contract and Telegram connection path

- Added a feature-flagged Google Calendar connection path reachable from the active Telegram
  menu. Authorization uses Authorization Code + PKCE, a single-use hashed state bound to the
  Telegram user/chat and exact HTTPS redirect, and a minimal `calendar.events` scope.
- The callback is exposed only through the public edge and forwarded with an authenticated
  request to the private worker. Refresh tokens are written to Secret Manager, never Firestore,
  Telegram, logs, or Gemini; disconnect revokes the Google token before deleting the secret.
- Added a per-traveler Calendar provider factory with idempotent effect markers and provider
  reread verification. Without a connected account, Calendar actions stop explicitly with
  `calendar_not_connected` and cannot be reported as successful.
- Calendar updates can now safely upsert the agent's deterministic itinerary event when no
  provider event ID exists, searching by the semantic effect marker first to avoid duplicates.
- Local verification: full pytest suite, Ruff, strict mypy, and `git diff --check` pass. The
feature remains disabled by default; no OAuth consent, secret, Cloud Run rollout, or Devpost
publication was performed.

## 2026-08-23 — Autonomous watcher failure truthfulness

- Trip Watch now persists bounded provider states (`PROVIDER_ERROR`, `AI_CONNECTION_REQUIRED`,
  `JUDGE_QUOTA_EXHAUSTED`, or malformed/empty-grounding errors) on a watchpoint when Search Watch
  or another grounder cannot produce evidence. Provider exception text is intentionally not
  chained into the persisted state, preventing credentials, URLs, or traveler data from leaking
  into Firestore.
- Amadeus failures remain explicitly `MONITORING_DEGRADED` and a later valid snapshot restores
  live coverage; the Telegram trip-status view counts both classes of degraded coverage.
- Affected informational signals stay in the durable pending queue until a source-linked Telegram
  notification is acknowledged. A missing gateway can therefore not silently turn an interruption
  into an apparently handled event.
- An Amadeus flight watchpoint with a missing trip, item, or live subscription binding now raises a
  bounded binding error; the scheduler persists it as degraded instead of silently reporting a
  healthy check.
- A watchpoint whose trip was deleted or never committed now records `TRIP_NOT_FOUND` after the
  atomic claim and raises a bounded configuration error. Orphaned checks therefore surface as
  degraded/retryable health instead of being mistaken for a successful no-op.
- Watch-tick error logs now carry only a bounded `error_code` and watchpoint/provider identifiers;
  raw provider exceptions and tracebacks are not emitted from the autonomous polling boundary.
- Unwrapped HTTP/client failures from the Amadeus adapter now also mark live coverage degraded and
  retry with `AMADEUS_PROVIDER_ERROR`; they cannot be mistaken for an on-time result.
- Added `scripts/run_canonical_e2e.sh` and `.github/workflows/quality.yml`: the autonomous
  watcher-to-approval-to-resume proof is reproducible from a clean Python environment, while
  CI covers backend tests/Ruff/mypy, landing build/Sites/browser smoke, production dependency
  audit, common credential-pattern rejection, and an immutable-container Trivy scan.
- The canonical runner passed three consecutive local rehearsals without manual state repair.
- Local verification: backend test suite, canonical E2E runner, Ruff, strict mypy,
  `git diff --check`, landing build, Sites packaging tests, browser smoke test, and production
  dependency audit pass. These changes are local and intentionally not deployed or published.

## 2026-08-23 — Grounding integrity and bounded analysis errors

- A Gemini response that claims an affected trip but has no matching HTTPS URL in Vertex
  grounding metadata is now `INVALID_GROUNDED_RESPONSE`, not a silent no-op. The watchpoint
  remains degraded and retryable, so an unverifiable alert cannot clear monitoring health.
- Known Amadeus transport/API failures now raise `AMADEUS_PROVIDER_ERROR` after marking the live
  subscription `MONITORING_DEGRADED`; an outage cannot be mistaken for an on-time flight.
- Impact-analysis failures persist only a bounded exception class/code and emit structured error
  metadata without a traceback. Provider URLs, credentials, and traveler details never become
  durable incident errors or routine worker logs.
- Regression coverage and the canonical autonomous E2E remain green after these changes; the
  latest code is still local and requires the controlled Cloud Run rollout before live proof.

## 2026-08-23 — Judge-mode shared Vertex budget

- Judge mode now injects an explicit `JudgeImpactInterpreter` into the Telegram impact path. A
  judge trip no longer waits for a personal Gemini/BYOK key, while normal traveler trips still
  require their own connected provider and never fall back to the shared project identity.
- Shared impact explanations consume one durable Firestore rate slot per day under the bounded
  `judge-mode-global` bucket. When the project allowance is exhausted or Vertex is unavailable,
  the deterministic impact engine remains authoritative and supplies a conservative explanation;
  no unbounded retry or hidden billing path is opened.
- Added regression coverage for explicit judge routing and daily-budget exhaustion. The latest
  local build remains the source of truth until a controlled Cloud Run rollout and live Telegram
  proof are completed.
- If the shared Vertex call is refused or unavailable, the persisted interpretation now labels
  that condition explicitly while deterministic impact/recovery continues; this is a visible
  bounded degradation, not a silent claim that Gemini answered.
- Planning follows the same truthfulness rule: when the shared Vertex planning call is exhausted
  or fails, Cloud Logging receives only a bounded fallback marker and Telegram labels every
  returned route as `estimate`; no estimate can be mistaken for live availability or a booking.
- Cloud Run deployment scripts now carry the complete autonomous runtime contract on a fresh
  revision: watcher/judge flags, model and Pub/Sub command settings, plus Telegram and approval
  credentials referenced only from Secret Manager. A worker rollout can no longer silently
  become a chat-only process because feature flags were omitted.
