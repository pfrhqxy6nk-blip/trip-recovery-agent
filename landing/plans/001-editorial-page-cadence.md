# 001 — Rebuild the page as a white-paper travel story

- **Status**: DONE
- **Commit**: bd9ca78
- **Severity**: HIGH
- **Category**: Missed opportunities / cohesion
- **Estimated scope**: 3 files, ~300 lines plus 3 generated raster assets

## Problem

The landing has a good Portal-inspired hero, but the rest compresses the entire product into three similar white sections. The current linear block sequence in `landing/src/App.jsx:42-46` is too short and has no visual escalation. The current final CTA also ends on a plain white surface, despite an existing landscape asset.

```jsx
// landing/src/App.jsx:42-46 — current
<section className="statement" id="how-it-works">...</section>
<section className="watch-section">...</section>
<section className="recovery-section" id="safety">...</section>
<section className="flight-film" id="flight-film">...</section>
<section className="final-cta">...</section>
```

The repeated 2-column/card composition in `landing/src/styles.css:38-42` makes a travel film feel like a short SaaS page.

## Target

Implement exactly eight visual chapters, preserving the hero as chapter one:

1. **Telegram home** — existing starry hero and real interactive conversation.
2. **What the agent watches** — editorial white section, six colored-dot capabilities, no product card.
3. **A live itinerary** — generated white-background route/boarding-pass asset beside a concise explanation of trip ingestion and watchpoints.
4. **Trust before action** — official-source and impact chain, expressed as three floating paper artifacts: `Signal → Verify → Impact`.
5. **The flight interlude** — the four-frame plane scene specified by plan 002.
6. **It acts quietly** — recovery confirmations in a deliberately asymmetric floating composition; no equal-size card grid.
7. **Costs stay visible** — a single approval-and-spending visual that explains the €20 threshold and why €34 returns to the user.
8. **Arrival** — a white final CTA with `/assets/trip-watch-footer-landscape-v1.png` rising from the bottom, like the Portal reference.

Each white chapter uses `max-width: 1200px`, `padding-block: clamp(112px, 13vw, 190px)`, and only one dominant visual idea. Use `#f7f7f7` paper, `#09090c` ink, `#4b4b50` copy, and `#0879f9` functional blue. Keep display type in DM Serif Display and UI/body in Inter. Use radius `22px` to `30px`, a 1px neutral border, and glow rings instead of large drop shadows.

## Repo conventions to follow

- The existing hero is the composition exemplar: `landing/src/App.jsx:37-41` and `landing/src/styles.css:10-27`.
- Existing colors and fonts are already defined in `landing/src/styles.css:3`; extend this root block rather than creating a second token system.
- Images belong under `landing/public/assets/` and are served with `/assets/...`, as at `landing/src/App.jsx:45`.

## Steps

1. In `landing/src/App.jsx`, replace the broad `statement`, `watch-section`, and `recovery-section` sequence at lines 42-44 with chapters 2, 3, 4, 6, and 7 above. Retain all truthful product language: only verified public signals trigger action; money, penalties, ambiguity, and irreversible changes require approval.
2. Generate three coherent 16:10 raster assets under `landing/public/assets/`: `paper-route-v1.png`, `paper-signal-v1.png`, and `paper-policy-v1.png`. They must have a warm paper-white field, refined ink/blue/pink travel artifacts, no readable fake brand names, no gradients that look like a UI mock, and enough empty edge space for crop-safe responsive use.
3. In `landing/src/styles.css`, replace repeated generic grids with alternating single-column editorial and offset two-column compositions. Do not add a feature-card grid. Give every chapter a distinct visual anchor and at least `112px` vertical breathing room.
4. In `landing/src/App.jsx`, use the existing `trip-watch-footer-landscape-v1.png` in the final section as a bottom-aligned decorative image with semantic `alt=""`; do not use it as CSS art.
5. Keep the existing interactive Telegram demo working: `Approve recovery`, `Replay demo`, and `Show details` must continue to function.
6. Make chapters stack in a single column below `760px`, with the visual after its text, 26px side padding, no clipped art, and no horizontal scrolling.

## Boundaries

- Do NOT change `landing/worker/index.js`, Sites packaging files, API/backend code, or deployment configuration.
- Do NOT invent provider integrations or state that the agent books a paid flight automatically.
- Do NOT use an unrelated stock image, generic gradient card, or a copied Portal asset.
- Do NOT add dependencies.
- If the existing landscape image is missing, stop and report; do not recreate it with CSS.

## Verification

- **Mechanical**: run `npm run build`, `npm run test:sites`, and `npm run test:browser`; all must pass.
- **Feel check**: at 1440×900, scroll from hero to footer. Confirm every chapter has one clear subject, the white paper rhythm has generous pauses, and the flight interlude feels like a purposeful midpoint instead of an end screen.
- **Mobile check**: at 390×844, inspect each chapter; confirm text is never behind an image and no page edge is clipped.
- **Done when**: the page has all eight chapters, its final landscape creates a visual landing, and none of the white sections feels like a repeated SaaS feature block.

## Delivery evidence — 2026-08-24

- All eight semantic chapters are present in `landing/src/App.jsx`, including the Telegram hero, watch, route, trust, flight, recovery, compensation, and arrival scenes.
- Original paper-world and landscape assets are local under `landing/public/assets/`; no Portal assets or external runtime images are used.
- Desktop/mobile layout and Telegram approval/replay behavior are covered by `landing/tests/browser-smoke.mjs`.
