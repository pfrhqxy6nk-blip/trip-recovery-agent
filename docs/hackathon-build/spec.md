# Technical Specification — Trip Recovery Agent

## 1. Architecture decision

Use two Cloud Run service boundaries built from the same repository and, if convenient, the same image:

```text
Telegram / Google OAuth
          |
          v
trip-agent-edge (public, minimal)
- Telegram secret verification
- update/callback deduplication
- OAuth state/PKCE callback
- no recovery authority
          |
          v
Pub/Sub workflow commands + durable outbox
          |
          v authenticated push
trip-recovery-agent (private worker)
- impact analysis
- ADK/Gemini
- deterministic planner/policy
- execution and verification
          |
          +--> Firestore (authoritative workflow state)
          +--> Telegram Bot API
          +--> Calendar / Gmail / Duffel / demo providers
          +--> Secret Manager
```

The existing private worker must not be made unauthenticated to accommodate Telegram. Route-level checks are not a substitute for a separate public ingress boundary.

For hackathon speed, use `APP_ROLE=edge|worker` to select routes in one container. The edge service validates, persists, and emits commands; it never mutates recovery state directly.

## 2. Responsibility boundaries

### Deterministic code owns

- connection feasibility and trip-graph impact;
- provider option admissibility and normalized facts;
- money in integer minor units;
- policy authority and mandatory approval rules;
- state transitions and compare-and-set versions;
- plan hashes, effect keys, leases, retries, and idempotency;
- approval binding, expiry, consumption, and supersession;
- provider verification and final recovery invariant.

### Gemini through ADK may

- interpret normalized provider/event text;
- explain deterministic impact in traveler language;
- rank admissible provider-supplied options;
- generate concise Telegram copy from structured facts;
- recommend replanning, subject to deterministic validation.

Gemini may not invent an option, price, penalty, reversibility guarantee, time, provider confirmation, or execution authority. All model output is Pydantic-validated and treated as untrusted input.

## 3. Domain model

Split the current compatibility model into focused modules.

### `Money`

```text
currency: uppercase three-letter code
minor_units: int
```

MVP permits EUR only. Currency mismatch never auto-executes. Avoid authoritative `Decimal` serialization in Firestore.

### `Traveler`

```text
user_id
telegram_user_id
telegram_chat_id
locale
timezone
onboarding_status
active_policy_version
created_at / updated_at
```

### `AutonomyPolicy`

```text
policy_id / version
notify_meaningful_changes = ALWAYS
calendar_mode = AUTO | ASK
service_message_mode = AUTO | ASK
reversible_change_mode = AUTO | ASK
automatic_spending_enabled
incident_spending_limit: Money | null
major_change_thresholds
created_at / updated_at
```

Mandatory approval rules are code-owned and cannot be disabled.

### `RecoveryOption`

```text
provider
provider_option_id
normalized_itinerary
incremental_cost: Money
penalty: Money | null
reversibility evidence/deadline
quote_expires_at
provider_snapshot_hash
```

### `RecoveryPlan`

```text
plan_id
incident_id
version
source_incident_version
impact_hash
policy_version
selected_option
ordered action DAG
total incremental cost
valid_until
canonical plan_hash
planner/model metadata
status = CURRENT | SUPERSEDED | EXPIRED | CANCELLED
```

### `PlannedAction`

```text
action_id
kind / provider / target
desired_state
prerequisites
cost: Money
reversible / reversible_until
penalty
ambiguous
major_change_reasons[]
verification_spec
policy_verdict / reason_codes[]
effect_key
status / lease / attempts summary
```

Use two identifiers:

- audit action ID includes incident and plan version;
- semantic effect key is stable across plan revisions: `incident:provider:target:operation:desired_state_hash`.

A revised plan that desires the same real-world state must not repeat the side effect.

### `ApprovalRequest`

```text
approval_id
incident_id / plan_version / plan_hash
policy_version
approved_action_ids
maximum_authorized: Money
option_fingerprint
expires_at
telegram_user_id / telegram_chat_id
callback_token_hash
status = PENDING | APPROVED | DECLINED | EXPIRED | SUPERSEDED
decided_at / consumed_update_id
```

The callback carries only `a:<opaque-token>`, `d:<opaque-token>`, or another short opaque token. Store only SHA-256(token) server-side.

### Action lifecycle

```text
BLOCKED → PENDING → LEASED → SUCCEEDED → VERIFYING → VERIFIED
                         ↘ FAILED_RETRYABLE → PENDING
                         ↘ FAILED_TERMINAL
SUCCEEDED/VERIFYING      ↘ VERIFICATION_FAILED
any unstarted action     ↘ SUPERSEDED | SKIPPED
```

Persist immutable `ActionAttempt` records with redacted metadata, latency, provider reference, retry class, and timestamps.

## 4. Incident state machine

```text
RECEIVED
→ ANALYZING
→ PLANNING
→ NOTIFYING
→ EXECUTING_AUTO
→ WAITING_APPROVAL
→ EXECUTING_APPROVED
→ VERIFYING
→ RECOVERED
```

Exception states:

- `RETRY_SCHEDULED` for resumable infrastructure/provider failures;
- `NEEDS_ATTENTION` for unresolved/manual conditions;
- `FAILED` for terminal internal failure;
- `CANCELLED` when the traveler stops this recovery.

Durable notification/action records may represent subphases, but every transition must be restart-safe. Notify before consequential side effects. `RECOVERED` requires the current plan, no pending approval, all required actions `VERIFIED` or deterministically optional-skipped, provider-confirmed itinerary, and zero deterministic conflicts.

## 5. Firestore layout

```text
travelers/{userId}
telegramUsers/{telegramUserId}
travelers/{userId}/policies/{version}
travelers/{userId}/connections/{provider}

trips/{tripId}
trips/{tripId}/items/{itemId}
trips/{tripId}/dependencies/{dependencyId}

processedEvents/{eventId}
incidents/{incidentId}
incidents/{incidentId}/plans/{planVersion}
incidents/{incidentId}/actions/{actionId}
incidents/{incidentId}/actions/{actionId}/attempts/{attemptId}
incidents/{incidentId}/approvals/{approvalId}
incidents/{incidentId}/notifications/{notificationId}
incidents/{incidentId}/auditEvents/{auditId}

effects/{effectHash}
telegramUpdates/{updateId}
callbackTokens/{tokenHash}
workflowCommands/{commandId}
outbox/{outboxId}
oauthStates/{stateHash}
demoProviderState/{resourceId}
```

Keep incident root documents small: status, current plan/version/hash, workflow cursor, owner, lease, summary, timestamps, and monotonic version. Do not embed growing attempts/audit arrays.

Every schema-v2 document includes `schema_version`. Existing cloud documents require an idempotent backfill/read-compatibility strategy before the worker switches writes.

### Gemini BYOK connection

`travelers/{userId}/connections/gemini` contains only:

```text
provider = GEMINI_API
mode = USER_MANAGED_KEY
secret_resource_name
key_fingerprint (one-way, masked for UI)
status = PENDING | CONNECTED | DISCONNECTED | INVALID
created_at / validated_at / disconnected_at
```

It never contains an API key, authorization header, token count, prompt, or model response. A separately stored `aiConnectionState` contains a hashed, single-use connection token, Telegram user/chat binding, expiry, and consumed time. The public edge validates that token before accepting a key, writes the secret directly to Secret Manager, then emits a non-secret connection command. The worker resolves the secret only at invocation time and never places it into an ADK session, Firestore document, exception, or log record.

The provider selector is explicit per traveler: `USER_MANAGED_GEMINI` or `SYSTEM_VERTEX`. There is no implicit fallback from a failed user key to the system connection; return `AI connection needs attention` instead. Vertex remains available only for internal/hackathon-demo identities that explicitly select it.

## 6. Transactional repository contract

Replace blind full-document `save_incident()` calls in all concurrent paths with explicit primitives:

```text
claim_disruption(event_id, payload_hash, lease)
transition_incident(expected_version, from_states, to_state, patch)
commit_impact_and_complete_event(...)
commit_plan(expected_incident_version, plan)
claim_action(action_id, expected_status, lease)
complete_action_and_create_effect_receipt(...)
consume_approval(...)
claim_telegram_update(update_id, payload_hash)
enqueue_outbox_once(...)
```

Every transaction checks expected version, allowed source state, relevant lease, current plan hash/version, and payload fingerprint. It increments the version and writes a deterministic audit event.

External calls never happen inside Firestore transactions.

Action pattern:

```text
transactionally claim
→ call provider with effect_key
→ transactionally store outcome/reference
→ independently reread provider
→ transactionally store verification
```

If the process dies after provider success but before local commit, the next worker checks `effects/{effectHash}` and provider state before deciding whether to retry.

Approval consumption transaction checks token, user/chat, ownership, status, incident state, plan hash/version, policy version, quote, amount, currency, expiry, and supersession. It consumes exactly once and creates a deterministic continuation outbox record atomically.

## 7. Workflow commands and delivery

Keep the existing `trip-disruptions` topic. Add, only during an explicitly authorized infrastructure milestone:

- `trip-workflow-commands` plus authenticated private-worker subscription;
- dead-letter topic/subscription;
- optional scheduled outbox sweeper.

Command types:

```text
START_RECOVERY
CONTINUE_WORKFLOW
RESUME_AFTER_APPROVAL
RETRY_ACTION
EXPIRE_APPROVAL
REPLAN
```

Commands are small, versioned, correlated, and idempotent. Do not depend on Pub/Sub ordering. Firestore CAS decides whether a command is still applicable.

The edge commits callback/update state and an outbox record atomically, publishes after commit, and marks delivery. Repeated webhooks or a sweeper may republish the deterministic outbox item safely.

## 8. Provider ports

```text
RecoveryOptionProvider
FlightRecoveryProvider
TransferProvider
CalendarProvider
ServiceMessagingProvider
TelegramGateway
OAuthTokenStore
WorkflowCommandPublisher
Clock
```

Every mutating port accepts an effect/idempotency key, returns a structured provider reference, classifies retryable versus terminal failures, and provides an independent verification method.

Implementation order:

1. Firestore-backed deterministic flight/transfer/hotel/calendar providers.
2. Real Telegram Bot API.
3. Real Google Calendar.
4. Duffel sandbox and Gmail as stretch.

Do not use in-memory provider state as recorded-demo evidence because Cloud Run restarts erase it.

## 9. Telegram edge security and UX

- Verify `X-Telegram-Bot-Api-Secret-Token` using constant-time comparison.
- Load secrets from Secret Manager/environment and never log them.
- Reject oversized/malformed bodies and unknown update shapes safely.
- Deduplicate `update_id` with payload fingerprints.
- Bind callbacks to exact Telegram user and chat.
- Rate-limit by user and update type.
- Never trust button text, callback values, cost, or plan data from the client.
- Keep `callback_data` under Telegram's 64-byte limit by using opaque tokens.
- Call `answerCallbackQuery` promptly, then edit/send status asynchronously.
- Persist Telegram chat/message IDs and delivery state.
- Do not send unrelated user free text to Gemini as an execution instruction.

If the bot is blocked or initial delivery cannot be made, persist `DELIVERY_BLOCKED`, retry, alert operationally, and do not begin new consequential actions.

## 10. OAuth and Google Workspace

Use Authorization Code with PKCE and a random single-use `state`. Store only the state hash, bound to traveler/chat, provider, redirect URI, requested scopes, verifier metadata, and expiry.

Use minimum incremental scopes:

- Calendar: event-level scope sufficient for app-managed events;
- Gmail: compose/send only if the selected action requires it; never broad inbox access.

Refresh/access tokens never enter Firestore, logs, Telegram, or Gemini prompts. For the one-user MVP, store refresh tokens in Secret Manager and only connection metadata/secret resource name in Firestore. Edge identity can add a secret version; worker identity can access it. Provide disconnect/revoke behavior.

Calendar mutation uses an app-owned effect key in extended properties and verification compares actual start/end/timezone/status after a reread. Gmail draft/send verification rereads the returned draft/message ID. Sending Gmail is irreversible and therefore approval-required.

## 11. Duffel sandbox boundary

Duffel is an adapter, not a prerequisite for the deterministic demo. Use sandbox/test mode, provider-supplied options only, and stable idempotency where supported. Validate:

- quote expiry;
- itinerary/option fingerprint;
- incremental price and EUR currency;
- change/penalty terms;
- final order/segment state after mutation.

Never store or process payment card data in this hackathon project. A changed price, route, cabin, penalty, or expired quote invalidates approval.

## 12. Observability and audit

Structured logs include:

```text
correlation_id, incident_id, trip_id, plan_version,
action_id, effect_hash, command_id, provider,
workflow_transition, result_class, attempt, latency_ms
```

Hash or omit Telegram identifiers. Redact tokens, authorization headers, email bodies, provider payloads, OAuth data, and callback tokens.

Persist immutable audit events for policy snapshots/decisions, plan creation/supersession, notifications, approval request/consumption/rejection, action claim/result/verification, and every state transition.

## 13. File plan

### Add

```text
backend/app/models/money.py
backend/app/models/policy.py
backend/app/models/recovery.py
backend/app/models/telegram.py
backend/app/models/oauth.py
backend/app/models/commands.py

backend/app/services/state_machine.py
backend/app/services/policy_engine.py
backend/app/services/recovery_planner.py
backend/app/services/canonical_hash.py
backend/app/services/approval_service.py
backend/app/services/action_executor.py
backend/app/services/verifier.py
backend/app/services/outbox.py

backend/app/providers/demo.py
backend/app/providers/telegram.py
backend/app/providers/google_calendar.py
backend/app/providers/gmail.py
backend/app/providers/duffel.py
backend/app/providers/oauth_tokens.py

backend/app/workflows/recovery.py
backend/app/workflows/workflow_commands.py

backend/app/api/internal.py
backend/app/api/telegram.py
backend/app/api/oauth.py

backend/app/migrations/backfill_schema_v2.py
.env.example
```

### Modify

```text
backend/app/config.py
backend/app/main.py
backend/app/logging.py
backend/app/models/enums.py
backend/app/models/domain.py
backend/app/models/__init__.py
backend/app/services/ports.py
backend/app/services/firestore.py
backend/app/services/memory.py
backend/app/api/routes.py
backend/app/workflows/impact_analysis.py
backend/app/demo_data.py
pyproject.toml
README.md
docs/ARCHITECTURE.md
docs/PRODUCT_SPEC.md
```

### Tests

```text
backend/tests/test_money.py
backend/tests/test_policy_engine.py
backend/tests/test_recovery_planner.py
backend/tests/test_state_machine.py
backend/tests/test_repository_contract.py
backend/tests/test_approvals.py
backend/tests/test_action_idempotency.py
backend/tests/test_verification.py
backend/tests/test_telegram_api.py
backend/tests/test_workflow_resume.py
backend/tests/test_recovery_e2e.py
```

## 14. Quality gates

Every implementation milestone must pass:

- `pytest` for its unit/contract/integration scope;
- Ruff format/lint;
- mypy with new workflow and adapter modules included;
- `git diff --check`;
- secret-pattern scan;
- no external network dependency in deterministic tests;
- no public deployment/push/submission without explicit authorization.

Before cloud proof, add CI for tests, type/lint, dependency audit, container scan, and secret scan. Evidence must tie the running revision to an immutable commit/image digest.

## 15. Threat cases that require tests

- forged Telegram secret header;
- malformed/oversized update;
- callback token theft, replay, or cross-user use;
- duplicate/out-of-order Pub/Sub delivery;
- payload collision on reused event ID;
- concurrent action claims and approval consumption;
- crash after external success but before Firestore commit;
- quote/approval time-of-check/time-of-use change;
- prompt injection in provider/event text;
- OAuth CSRF/state replay and leaked refresh token;
- public invocation of private worker routes;
- provider retry causing duplicate side effect;
- false `Trip recovered` after incomplete verification.

## 16. External API contracts

- Telegram Bot API webhook and callback behavior: <https://core.telegram.org/bots/api>
- Google Calendar authorization scopes: <https://developers.google.com/workspace/calendar/api/auth>
- Google Calendar event update: <https://developers.google.com/workspace/calendar/api/v3/reference/events/update>
- Gmail message send: <https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send>
- Duffel test integration: <https://duffel.com/docs/api/overview/test-your-integration>
- Duffel webhook events: <https://duffel.com/docs/api/v2/webhook-events>
- Duffel order changes: <https://duffel.com/docs/api/order-changes>

These links are implementation references, not authorization to call or configure the services.
