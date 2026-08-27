# Architecture

## Milestone 01 flow

```text
API / simulator
    |
    v
Pub/Sub topic: trip-disruptions
    |
    v  authenticated push
Cloud Run: POST /internal/pubsub/disruptions
    |
    v
Firestore transaction
  - create processedEvents/{eventId}
  - create incidents/{stableIncidentId}
    |
    v
Deterministic impact engine <--- seeded trip/items/dependencies
    |
    +-- authoritative impact snapshot persisted
    |
    v
Google ADK Agent -> configurable Gemini model on Vertex AI
    |
    +-- Pydantic validation boundary
    v
Incident interpretation persisted; event marked completed
```

Approval resume is also event-driven and durable:

```text
Telegram approval callback
    |
    v  Firestore transaction (claim approval, commit continuation outbox)
trip-workflow-commands topic
    |
    v  authenticated push
Cloud Run: POST /internal/pubsub/commands
    |
    v
RecoveryWorkflow resumes from the persisted plan
    |
    +-- idempotent provider actions + verification
    v
Telegram: Trip recovered / needs attention
```

The callback performs no recovery work inline. It commits an owner-bound continuation
command first; fast dispatch is best effort, while `/internal/watch/tick` retries the
same command ID from the durable outbox when the worker or Pub/Sub is temporarily down.

## Atomicity and retry behavior

The stable incident ID is derived from the external event ID. The first delivery creates
the processed-event ownership document and placeholder incident in one Firestore
transaction. A concurrent delivery observes the active lease and returns a retryable
non-2xx response, so it cannot accidentally acknowledge work owned by a process that has
just crashed. Once the original work completes, a redelivery is acknowledged without
reprocessing. A delivery after a retryable failure or expired lease atomically reacquires
the same event and resumes the same incident.

Authoritative deterministic impact is persisted before invoking Gemini. Invalid model
output therefore cannot modify feasibility or dependency calculations. It records a
retryable failure while preserving that snapshot.

## Ports and adapters

Workflow code depends on repository, publisher, and interpretation protocols. Production
adapters use Firestore, Pub/Sub, and ADK/Vertex AI. In-memory implementations provide a
local processing mode and deterministic concurrency tests.

## Persistence

- `trips/{tripId}`: trip header.
- `trips/{tripId}/items/{itemId}`: typed travel items.
- `trips/{tripId}/dependencies/{dependencyId}`: directed graph edges and buffers.
- `processedEvents/{eventId}`: claim status, lease, attempts, incident ownership.
- `incidents/{incidentId}`: lifecycle, correlation ID, deterministic impact, separate
  Gemini interpretation, model/prompt metadata, errors, retries, and timestamps.

The current Milestone 01 action key is only a compatibility placeholder:
`{incident_id}:{plan_version}:{action_type}:{target_external_id}`. Schema-v2 will retain
an audit action ID while introducing a semantic effect key based on provider, target,
operation, and desired-state hash. This prevents a revised plan from repeating an already
completed real-world effect.

Future approvals bind to incident ID, current plan version/hash, policy version, quote
fingerprint, amount, currency, Telegram owner/chat, and expiry. Any changed input
invalidates approval.

## Baseline provenance and schema-v2 boundary

The tested production slice is documented in `CLOUD_PROOF.md`: root `Dockerfile`, private
Cloud Run service, authenticated Pub/Sub push, ADK/Gemini via Vertex AI, Firestore
persistence, and duplicate-event suppression. That proof is tied to Milestone 01 source
revision `bd9ca78` plus local, currently uncommitted deployment-support files. It is not
yet immutable source-to-image provenance and must not be presented as such.

The present repository's `save_incident()` operation remains compatible with Milestone 01
single-workflow processing. It must not be used for Telegram callbacks, approvals, action
claims, plan commits, or provider execution. Those paths begin only after schema-v2 adds
explicit compare-and-set repository operations, version checks, leases, effect receipts,
and an idempotent backfill/read-compatibility path.

## Production security boundary

The push endpoint is not a public webhook. Pub/Sub uses an OIDC service account and Cloud
Run IAM permits only that identity to invoke the service. Application-level webhook
authentication is deferred because no third-party webhook exists in this milestone.
