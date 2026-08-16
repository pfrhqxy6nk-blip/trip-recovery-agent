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

Future action keys have the stable form
`{incident_id}:{plan_version}:{action_type}:{target_external_id}`. Future approvals bind
to incident ID, plan version/hash, quote, currency, and expiry; any changed input
invalidates approval.

## Production security boundary

The push endpoint is not a public webhook. Pub/Sub uses an OIDC service account and Cloud
Run IAM permits only that identity to invoke the service. Application-level webhook
authentication is deferred because no third-party webhook exists in this milestone.
