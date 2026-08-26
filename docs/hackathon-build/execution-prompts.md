# Execution Prompts — Trip Recovery Agent

These prompts are intended for future Codex/agent turns after the user explicitly starts implementation. Run them in order. Do not parallelize milestones that share domain, repository, workflow, or API contracts. Parallel work is safe only after the current milestone's interfaces and tests are accepted, with exclusive file ownership.

## Universal preamble for every implementation agent

Prepend this context to every prompt:

> Work in `/Users/oleksandrkoriahin/Desktop/Trip_Agent`. Read `AGENTS.md` if present, then read `docs/hackathon-build/prd.md`, `docs/hackathon-build/spec.md`, `docs/hackathon-build/checklist.md`, the current repository code, tests, and git status before editing. Preserve user changes and the proven Milestone 01 behavior. Firestore is authoritative; deterministic code owns money, time, authority, state, idempotency, and verification. Gemini may only interpret/rank/explain validated facts. Use `apply_patch` for edits. Add or update tests with every behavior. Run relevant pytest, Ruff, mypy, `git diff --check`, and a secret scan. Do not print secret values. Do not push, publish, deploy, change cloud resources, set webhooks, open OAuth consent, commit, or submit to Devpost unless the user explicitly authorizes that exact action. Report files changed, tests run, remaining risks, and the next safe milestone.

## Prompt 0 — Baseline and product contract

> **Objective:** Freeze an auditable implementation baseline without changing runtime behavior.
>
> **Tasks:**
> 1. Inspect git status, recent commits, existing cloud-proof documents, root/backend Dockerfile placement, and current test configuration.
> 2. Reconcile `docs/PRODUCT_SPEC.md` and `docs/ARCHITECTURE.md` with the approved PRD/spec while preserving useful existing content.
> 3. Add `.env.example` with variable names, descriptions, and safe placeholders only.
> 4. Document schema-v1 compatibility, source provenance for the already deployed proof, and the no-blind-save rule for future concurrent paths.
> 5. Do not refactor runtime code in this milestone.
>
> **Expected files:** documentation, `.env.example`, and test/tool configuration only if required to make the baseline reproducible.
>
> **Tests/gates:** run the existing full suite, Ruff, mypy, `git diff --check`, and a credential-pattern scan. Compare working-tree changes against user-owned changes; do not discard anything.
>
> **Definition of done:** the baseline is reproducible, current behavior remains green, cloud proof can be traced to source limitations, and no secret appears in tracked files.
>
> **Prohibited:** runtime feature work, dependency upgrades unrelated to reproducibility, commit/push/deploy/publication.

## Prompt 1 — Domain, policy, hashing, and transitions

> **Objective:** Implement schema-v2 domain foundations and exhaustive deterministic authority rules.
>
> **Add:** `backend/app/models/money.py`, `policy.py`, `recovery.py`, `telegram.py`, `oauth.py`, `commands.py`, `backend/app/services/canonical_hash.py`, `policy_engine.py`, and `state_machine.py`.
>
> **Modify carefully:** `models/enums.py`, `models/domain.py`, `models/__init__.py`, and compatibility serialization only.
>
> **Requirements:**
> - `Money(currency, minor_units)` with safe addition/comparison and EUR-only MVP validation.
> - Versioned traveler policy and immutable mandatory approval overrides.
> - Orthogonal safety facts: cost, penalty, reversible/deadline, ambiguous, major-change reasons, category.
> - Versioned recovery option, plan, action DAG, approval binding, commands, and schema version.
> - Canonical JSON/hashing rules with timezone normalization and stable collection ordering.
> - Separate audit `action_id` and semantic `effect_key`.
> - Explicit allowed incident/action state transitions.
> - Backward parsing for current incident documents where needed; do not silently reinterpret old money fields.
>
> **Tests:** policy truth table including +€34 vs €20, cumulative spending, every mandatory override, ASK/AUTO categories, currency mismatch, stable hashes, hash changes for meaningful fields, illegal transitions, and backward parsing.
>
> **Definition of done:** all authority decisions are deterministic and explainable with reason codes; no model output can override them; types and tests are green.
>
> **Prohibited:** Firestore transaction implementation, provider adapters, Telegram routes, cloud work.

## Prompt 2 — Transactional repository core

> **Objective:** Replace unsafe concurrent full-document writes with a contract shared by memory and Firestore adapters.
>
> **Modify:** `backend/app/services/ports.py`, `firestore.py`, `memory.py`, and workflow callers only as needed for compilation/backward behavior.
>
> **Add:** repository contract tests and `backend/app/migrations/backfill_schema_v2.py`.
>
> **Implement primitives:** `claim_disruption` with payload hash; expected-version incident transition; atomic impact/event completion; plan commit; action claim/lease; action result plus effect receipt; approval consume plus continuation outbox; Telegram update claim; deterministic outbox enqueue/claim/deliver marker.
>
> **Constraints:**
> - every mutation checks allowed source state/current plan where relevant and increments version;
> - external calls never occur inside transactions;
> - growing attempts/audit data lives in subcollections;
> - deterministic IDs make retries safe;
> - reused external IDs with different fingerprints are conflicts, not duplicates;
> - backfill is idempotent and dry-run capable.
>
> **Tests:** one shared adapter contract, concurrent winner tests, lease expiry/reclaim, payload collision, atomic approval/outbox, stable effect receipt, duplicate outbox, and Firestore emulator tests when available. If emulator is unavailable, document the gap and preserve a runnable emulator test target.
>
> **Definition of done:** no Telegram/action/approval path depends on blind `save_incident`; concurrency produces one authoritative winner; current ingestion tests remain green.
>
> **Prohibited:** live Firestore migration, external calls, deployment, Telegram/provider feature work.

## Prompt 3 — Recovery planner and persistent demo providers

> **Objective:** Produce one deterministic, versioned recovery plan and provider state that survives restart.
>
> **Add:** `recovery_planner.py`, provider port definitions, and `backend/app/providers/demo.py`.
>
> **Requirements:**
> - normalize provider-supplied options and reject invalid/expired/inconsistent options;
> - let Gemini rank/explain only validated option IDs; ignore invented IDs/facts;
> - build the Warsaw → Munich → Lisbon plan arriving 23:15, +€34, with transfer, hotel late-arrival record, calendar, and replacement-flight actions;
> - build an ordered action DAG and persist authority decisions/reasons;
> - persist demo provider resources in Firestore or the repository abstraction, never process memory;
> - supersede plans on new impact, policy, quote, option, or provider snapshot;
> - return the same plan for identical authoritative input.
>
> **Tests:** canonical 105-minute disruption, invalid Gemini option, expired quote, policy change, identical-input idempotency, plan supersession, effect-key stability across plans, and restart persistence.
>
> **Definition of done:** one canonical plan is deterministic, inspectable, policy-split, and restart-safe; provider breadth is not added.
>
> **Prohibited:** Telegram, real Calendar/Duffel/Gmail, cloud resources.

## Prompt 4 — Executor and verification

> **Objective:** Execute planned actions exactly once in effect and prove their external state.
>
> **Add:** `approval_service.py`, `action_executor.py`, `verifier.py`, action attempt/effect repository support, and focused tests.
>
> **Requirements:**
> - claim one eligible action using dependencies, status, lease, and expected plan;
> - call providers with semantic effect keys;
> - persist structured attempts and retryable/terminal classification;
> - after an uncertain outcome, query effect/provider state before retry;
> - independently reread desired state for every action;
> - calculate executed cost against authorized cost;
> - implement final trip conflict check and a single `can_mark_recovered` invariant;
> - final notification failure may retry notification but never repeat provider actions.
>
> **Fault-injection tests:** crash before call, success then lost response, success then crash before commit, duplicate command, lease theft prevention, plan revision with identical desired effect, provider 2xx/mismatched reread, verification retry/exhaustion, unresolved dependency, pending approval, and cost mismatch.
>
> **Definition of done:** duplicate/restart paths create one semantic effect; false success is structurally impossible; attempts are auditable and redacted.
>
> **Prohibited:** UI routes, real network adapters, cloud changes.

## Prompt 5 — Persistent workflow orchestration

> **Objective:** Implement the complete recovery state machine as bounded, resumable commands.
>
> **Add:** `backend/app/workflows/recovery.py`, `workflow_commands.py`, and `backend/app/services/outbox.py`.
>
> **Modify:** current impact workflow so successful impact commits enqueue/start recovery without breaking duplicate suppression.
>
> **Requirements:**
> - commands: start, continue, resume after approval, retry action, expire approval, replan;
> - each command performs bounded work and persists its cursor before returning;
> - phases: plan, notification intent/delivery, auto-actions, approval wait, approved actions, verify, final notification;
> - new disruption/policy/quote changes supersede stale plan/approval;
> - stop recovery preserves completed actions and marks unresolved trip risk;
> - no reliance on Pub/Sub ordering or process memory;
> - notification gateway is a port with a deterministic test implementation.
>
> **Tests:** full local canonical E2E, restart after every boundary, duplicate/out-of-order commands, timeout/retry, new disruption during approval, stale approval, cancellation/resume, no recovery option, all-auto path, no-auto path, and final-notification retry.
>
> **Definition of done:** the complete business workflow runs locally from disruption to verified result using persistent demo adapters.
>
> **Prohibited:** real Telegram/Google/Duffel calls, infrastructure creation.

## Prompt 6 — Telegram onboarding and approval edge

> **Objective:** Build the secure traveler-facing Telegram interaction against existing workflow ports.
>
> **Add:** `backend/app/api/telegram.py`, Telegram models/services, and a fake Telegram gateway.
>
> **Modify:** `config.py`, `main.py`, and route composition for `APP_ROLE=edge|worker` without making worker routes public.
>
> **Requirements:**
> - exact seven-step onboarding from the PRD, resumable and idempotent;
> - versioned settings updates;
> - constant-time webhook secret verification, body limit, schema validation, update fingerprint/dedupe;
> - hashed opaque callback tokens within Telegram limits;
> - exact user/chat/ownership binding and single-purpose/expiry rules;
> - awareness, consolidated approval, details, approve, find-another, stop-confirm, resume, in-progress, needs-attention, and final message rendering;
> - callback acknowledgement is immediate; durable work is enqueued;
> - free text cannot become an execution instruction.
>
> **Tests:** every onboarding branch/restart, invalid spending, repeated `/start`, forged secret, malformed body, duplicate update, duplicate/concurrent click, cross-user/chat, expired/stale token, read-only details, blocked bot/delivery failure, and message claims matching verification state.
>
> **Definition of done:** the canonical flow works through fake Telegram and all hostile callback cases are safe.
>
> **Prohibited:** setting a Telegram webhook, real bot token use, public deployment, direct state mutations outside repository/workflow services.

## Prompt 7 — Real Telegram adapter and edge hardening

> **Objective:** Connect the tested Telegram port to the Bot API without changing recovery semantics.
>
> **Add:** `backend/app/providers/telegram.py` and adapter contract tests with mocked HTTP.
>
> **Requirements:** retry-safe `sendMessage`, `editMessageText`, and `answerCallbackQuery`; Telegram error classification; safe formatting/escaping; timeouts; redacted HTTP logs; persisted external message IDs; delivery retry without action replay; minimal edge health/webhook routes only.
>
> **Verification:** mocked 2xx, 429 retry-after, 5xx, timeout after potential send, edit-not-modified, blocked bot, malformed response, and duplicate delivery. Run a local request test using a placeholder secret.
>
> **Definition of done:** adapter behavior is deterministic under Telegram retries and no credential/PII leaks.
>
> **Explicit pause:** report the exact environment variables and proposed edge/worker IAM boundary. Do not use a real bot token, set a webhook, or deploy until the user authorizes it.

## Prompt 8 — Google OAuth and Calendar

> **Objective:** Add least-privilege Google authorization and one independently verified real downstream action.
>
> **Add:** `backend/app/api/oauth.py`, `providers/oauth_tokens.py`, `providers/google_calendar.py`, and tests.
>
> **Requirements:** Authorization Code + PKCE; random hashed single-use state bound to user/chat/provider/scopes/redirect/expiry; exact redirect URI; minimal Calendar event scope; Secret Manager refresh-token storage with Firestore metadata only; refresh, disconnect, revoke; semantic effect key in private extended properties; get/update with concurrency protection; reread verification of start/end/timezone/status/effect marker.
>
> **Tests:** state replay, expiry, cross-user, redirect mismatch, missing code, token redaction, refresh failure, duplicate calendar update, concurrency conflict, mismatched reread, disconnect, and not-connected optional behavior. Mock all Google calls.
>
> **Definition of done:** local/mocked OAuth and Calendar contracts pass and absence of authorization is represented truthfully.
>
> **Explicit pause:** do not open consent, store a real token, change Cloud OAuth settings, or deploy without approval.

## Prompt 9 — Authorized cloud E2E

> **Run only after the user explicitly authorizes cloud mutations and deployment.**
>
> **Objective:** Prove real cloud disruption → private worker → Gemini → Firestore → Telegram → approval → resume → verified Calendar/demo-provider recovery.
>
> **Preflight:** show the user the exact proposed resource changes, IAM principals/roles, commands, secrets by name, rollback plan, and estimated externally visible surfaces. Preserve existing `tripagent-505715` resources.
>
> **Tasks after approval:** deploy immutable edge/worker revisions; create only the missing workflow topic/subscription/dead-letter/outbox trigger; grant least privilege; store secrets; set Telegram webhook to edge; configure OAuth callback if separately authorized; run canonical event and capture correlation IDs, revision/image digest, Firestore state, Telegram evidence, and provider rereads; replay event, command, and callback; deliberately interrupt one phase and prove resume.
>
> **Success gate:** one end-to-end trace, no duplicate effects/calls, private worker remains inaccessible publicly, all traveler claims match persisted/provider state, rollback documented.
>
> **Prohibited:** GitHub push, Devpost submission, unrelated infrastructure, broad IAM roles, secret output.

## Prompt 10 — Duffel and Gmail stretch

> **Run only if core milestones and three demo rehearsals are green.**
>
> **Objective:** Add optional live provider adapters without making the canonical demo fragile.
>
> **Duffel:** sandbox/test mode, normalized offers/options only, option/quote/penalty/expiry fingerprint, stable idempotency, order-change execution where supported, order/segment reread, no card data.
>
> **Gmail:** incremental minimum scope; create-draft may be policy-controlled reversible work, but send is always irreversible and approval-required; reread returned draft/message ID; never access inbox contents broadly.
>
> **Tests:** quote expiry/change after approval, price/route/currency/penalty drift, duplicate request, timeout/uncertain outcome, mismatched order reread, Gmail duplicate draft/send, revoked token, and redaction.
>
> **Definition of done:** adapters satisfy existing ports and contract tests; deterministic providers remain selectable fallback; Telegram never labels unverified/sandbox work as real production confirmation.
>
> **Prohibited:** production payment data, live booking outside sandbox, broad Gmail scopes, deployment without approval.

## Prompt 11 — Release QA and evidence

> **Objective:** Make the hackathon build reproducible, secure, and demonstrable without publishing it.
>
> **Tasks:** add CI configuration for full tests/Ruff/mypy/dependency audit/secret scan/container scan; include previously excluded workflow-critical modules in mypy; create a redacted canonical E2E runner; produce local architecture diagram, runbook, failure matrix, demo script, evidence index, and rollback instructions; tie evidence to commit/image digest; rehearse canonical success three times plus duplicate-click, restart-resume, stale-quote, and verification-failure stories.
>
> **Acceptance:** clean-environment build succeeds; all suites/scans pass or have explicit risk acceptance; three success rehearsals need no manual repair; failure scenario never emits `Trip recovered`; evidence contains no secrets/PII; demo can fall back to deterministic providers while still showing real Gemini/Telegram/cloud state clearly.
>
> **Explicit pause:** prepare local submission materials only. Do not push, upload video, make a repository/revision public, or submit to Devpost until the user explicitly says to publish.

## Orchestration prompt for the primary agent

Use this when the user is ready to execute the whole plan:

> Execute `docs/hackathon-build/checklist.md` milestone by milestone using `docs/hackathon-build/execution-prompts.md`. Start with the first incomplete milestone. Before edits, inspect repository state and preserve user changes. At each milestone, use subagents only for bounded read-only audits or non-overlapping work; the primary agent owns integration and must personally review skill instructions and shared contracts. Run every milestone's gates, show concise evidence, and stop at explicit approval gates for credentials, cloud changes, deployment, push, or publication. Do not skip failed gates or claim external verification from demo state. Maintain a local progress record in `docs/hackathon-build/build-notes.md` and update the checklist status after verified completion.
