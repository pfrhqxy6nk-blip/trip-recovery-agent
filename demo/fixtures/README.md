# Trip Watch beta fixtures

These files are synthetic, safe-to-share inputs for the hackathon demo. They are
explicitly marked **DEMO ONLY / NOT VALID FOR TRAVEL**. They contain no real PNR,
payment, passport, airline, or hotel reservation.

## Judge flow

1. Open [@tripagentai_bot](https://t.me/tripagentai_bot) and send `/start`.
2. Tap **Start my trip**, complete the short policy setup, then forward
   `warsaw-munich-lisbon-booking.pdf` to exercise multimodal PDF intake. Its explicit text
   layer is also parsed safely when Gemini is unavailable.
3. If using a PDF without a readable text layer, forward
   `warsaw-munich-lisbon-booking-email.txt` as a document or paste its text; the deterministic
   parser requires explicit ISO-8601 times.
4. Forward `warsaw-munich-lisbon-demo.pkpass` to exercise the safe `pass.json` parser.
5. Use `airport-board-delay.txt` as a synthetic disruption signal in the simulator.

The hidden `/demo` command is only a controlled deterministic fallback for maintainers;
it is not part of the normal first-user path and does not replace forwarding a booking
fixture.

The fixtures intentionally describe one coherent route: Warsaw → Munich → Lisbon,
with a hotel and airport transfer. This lets the agent build a dependency graph,
compute the blast radius of a 105-minute delay, screen visa/baggage risk, and show
policy-gated recovery without touching a real booking.

Regenerate the pack after changing the content:

```bash
.venv/bin/python scripts/build_beta_fixtures.py
```

The PDF is a visual handoff artifact; the `.pkpass` is a minimal unsigned demo pass,
not an Apple Wallet credential.
