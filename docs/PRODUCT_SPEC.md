# Product specification

## Product

Trip Recovery Agent is a Telegram-first autonomous travel agent. It can help a traveler
plan a bounded trip from a natural-language brief, ingest an existing booking artifact,
and then watch the saved itinerary. A change to one item triggers dependency-aware
analysis of the remaining journey. The system repairs policy-approved safe consequences
autonomously and requests approval only for actions that cross the traveler's authority
boundary.

Planning results are clearly labelled estimates until the traveler forwards real booking
evidence; the agent never claims to have booked an option or discovered private bookings
from the open web.

## Core loop

```text
change -> detect -> calculate blast radius -> plan -> execute under policy
       -> request approval when required -> verify external state -> recovered
```

## Judge journey

The canonical trip is Warsaw -> Munich -> Lisbon and contains a first flight, connecting
flight, airport transfer, and hotel arrival. A 105-minute delay makes the Munich
connection infeasible and affects every dependent item downstream. Judges start with
`/start`, activate the short policy setup, forward the repository's labelled beta fixture,
and then observe the disruption → impact → safe actions → approval → persistent resume flow.

## Product principles

- Deterministic code owns time, graph, cost, policy, state transition, and idempotency
  decisions.
- Gemini reasons over validated structured data and handles ambiguity, interpretation,
  communication, and replanning.
- External side effects are scoped, idempotent, observable, and verified by rereading the
  external system.
- Irreversible, ambiguous, penalty-bearing, stale, and materially different actions always
  require approval. A financial action may be automatic only when it is within the
  traveler's cumulative per-incident spending limit and has none of those risk flags.
- Firestore is the persistent source of truth; no workflow depends on one process living
  for its entire duration.

## Baseline and schema evolution

Milestone 01 ends at `PLANNING` after deterministic impact and a validated Gemini
interpretation are persisted. Existing `Incident`, `Action`, and `Approval` shapes are
schema-v1 compatibility models, not the authoritative recovery contract described in
`docs/hackathon-build/spec.md`.

Schema-v2 will introduce versioned policies, plans, approval decisions, semantic effect
keys, action attempts, verification records, and compare-and-set transitions. Until that
migration is complete, no new concurrent workflow path may rely on full-document
`save_incident()` overwrites.

## Status models

Trip: `HEALTHY`, `AT_RISK`, `RECOVERED`.

Incident: `RECEIVED`, `ANALYZING`, `PLANNING`, `WAITING_APPROVAL`, `EXECUTING`,
`VERIFYING`, `RECOVERED`, `FAILED`, `CANCELLED`.

Action safety: `INTERNAL_REVERSIBLE`, `EXTERNAL_REVERSIBLE`, `FINANCIAL`,
`IRREVERSIBLE`, `AMBIGUOUS`.

## Explicit production boundaries

Real airline rebooking, payments, and hotel/transfer mutations remain disabled until
provider credentials, OAuth consent, and reread verification are configured. Calendar
and Gmail paths are implemented as explicit opt-in, connection-gated adapters; the first
hackathon pilot uses safe demo providers and never fabricates a completed external action.
