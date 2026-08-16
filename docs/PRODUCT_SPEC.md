# Product specification

## Product

Trip Recovery Agent is an autonomous recovery layer for already-booked trips. A change
to one item triggers dependency-aware analysis of the remaining journey. The system
repairs safe, reversible consequences autonomously and requests approval for money,
irreversible changes, ambiguity, or materially different travel plans.

It is not a generic travel assistant, itinerary generator, or chatbot.

## Core loop

```text
change -> detect -> calculate blast radius -> plan -> execute under policy
       -> request approval when required -> verify external state -> recovered
```

## Demo journey

The canonical trip is Warsaw -> Munich -> Lisbon and contains a first flight, connecting
flight, airport transfer, and hotel arrival. A 105-minute delay makes the Munich
connection infeasible and affects every dependent item downstream.

## Product principles

- Deterministic code owns time, graph, cost, policy, state transition, and idempotency
  decisions.
- Gemini reasons over validated structured data and handles ambiguity, interpretation,
  communication, and replanning.
- External side effects are scoped, idempotent, observable, and verified by rereading the
  external system.
- Financial, irreversible, ambiguous, and materially different actions require approval.
- Firestore is the persistent source of truth; no workflow depends on one process living
  for its entire duration.

## Status models

Trip: `HEALTHY`, `AT_RISK`, `RECOVERED`.

Incident: `RECEIVED`, `ANALYZING`, `PLANNING`, `WAITING_APPROVAL`, `EXECUTING`,
`VERIFYING`, `RECOVERED`, `FAILED`, `CANCELLED`.

Action safety: `INTERNAL_REVERSIBLE`, `EXTERNAL_REVERSIBLE`, `FINANCIAL`,
`IRREVERSIBLE`, `AMBIGUOUS`.

## Out of scope for Milestone 01

Duffel, rebooking, Gmail, Calendar, notifications, authentication UI, approval UI, and
the frontend are intentionally deferred.
