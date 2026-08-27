# Showcase 01 — Telegram-first judge flow

This is the primary, human-readable path for a judge. It starts with an ordinary
Telegram conversation and uses the same multimodal intake a traveler would use in
the product. The synthetic files are safe demo artifacts and are clearly marked
**DEMO ONLY / NOT VALID FOR TRAVEL**.

## Primary judge flow

1. Open [@tripagentai_bot](https://t.me/tripagentai_bot) and send `/start`.
2. Tap **Start my trip**, complete the short autonomy policy, and continue in plain
   English chat. There is no persistent keyboard after activation.
3. Forward
   `demo/fixtures/warsaw-munich-lisbon-booking.pdf` from this repository as a Telegram
   document. The PDF contains an explicit text layer, so the bounded offline parser can
   extract the itinerary even when Gemini is unavailable.
4. Review the extracted draft (LO351, Munich connection, LH1790, hotel and PNR), then tap
   **Save trip**. The agent persists the trip and creates focused watchpoints; it does not
   invent a booking or claim that a synthetic reservation is real.
5. For the planning story, write `I want to go to Paris for 6 nights, budget €600`. The
   agent returns three clearly labelled estimates. These are planning options, not bookings.
6. In the controlled pilot, inject the signed 105-minute LO351 disruption. The expected
   sequence is awareness → impact → verified safe actions → approval request for the +€34
   flight → persistent resume after approval → verified `RECOVERED`.
7. Replay the event and approval callback. Idempotency must prevent duplicate effects or
   messages. Run **Stop recovery** in a fresh incident and confirm the consequential flight
   action is not executed.

## Local proof without external services

The deterministic domain showcase is useful for a repeatable terminal recording:

```bash
PYTHONPATH=backend .venv/bin/python -m app.demo_recovery
```

It proves the same 105-minute delay, Munich blast-radius calculation, three policy-safe
updates, €34 versus €20 approval boundary, persistent resume, provider rereads, and final
`RECOVERED` state. It uses local provider adapters and never sends Telegram messages or
changes an external booking.

## What this demonstrates

- Multimodal Telegram intake from a PDF, with explicit evidence metadata retained.
- A trip dependency graph connecting flights, transfer, hotel and calendar context.
- Deterministic impact analysis and Visa & Baggage Guardian checks.
- Autonomous, policy-gated recovery with approval only for consequential spend.
- Durable workflow state, idempotent callbacks and verified provider outcomes.

## Honest boundary

The normal judge path requires the owner-approved Cloud Run rollout and a fresh Telegram
smoke test. Search grounding, real Calendar/Gmail actions, airline booking changes and
payments remain feature- or provider-gated. Synthetic fixtures demonstrate parsing and
workflow behavior only; they are never represented as live travel reservations.
