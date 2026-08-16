# Milestone 01: persistent disruption impact

## Goal

A real event is published, atomically claimed, evaluated against a seeded trip graph,
interpreted by Gemini through ADK, and persisted exactly once.

## Acceptance criteria

Given `demo-trip-001` and two concurrent deliveries of one valid disruption:

- one initial claim wins and one stable incident exists;
- duplicate delivery cannot create another incident;
- connection feasibility and affected dependencies are deterministic;
- Gemini produces a separate schema-validated interpretation;
- invalid Gemini output cannot overwrite deterministic state;
- model ID, prompt version, correlation ID, and timestamps are persisted;
- an interrupted/retryable workflow resumes the same incident;
- all behavior above is covered by automated tests.

## Demo fixture

`demo-trip-001` has four items:

1. `flight-lo351`: Warsaw to Munich, arriving 18:00 UTC.
2. `flight-lh1792`: Munich to Lisbon, departing 18:55 UTC.
3. `airport-transfer`: Lisbon airport to city.
4. `hotel-arrival`: hotel check-in arrival window.

The first dependency requires a 45-minute connection buffer. Moving LO351 arrival to
19:45 UTC makes it infeasible and propagates impact through the remaining graph.

## Completion boundary

The milestone ends with incident status `PLANNING`, meaning impact analysis is complete
and the next milestone can produce recovery options. No external travel action is taken.
