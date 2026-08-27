# Trip Watch landing — visual QA

## Reference target

- User-supplied Portal screenshots and `DESIGN-3.md`.
- Required translation: preserve Portal's scene cadence — twilight hero and framed product surface, long white editorial world, sparse blue/pink/green details, and an illustrated landscape landing — while using original Trip Watch visuals and truthful product copy.

## Local evidence — 2026-08-22

- Desktop capture: 1440 × 900.
- Mobile capture: 390 × 844.
- Local preview: `http://127.0.0.1:5173/`.
- Reviewed captures: `/private/tmp/trip-watch-new-hero.png`, `new-route.png`, `new-sources.png`, `new-poster.png`, `new-recovery.png`, `new-footer.png`, and `new-mobile.png`.

## Passed

- Hero uses the supplied starry twilight direction and a large white framed Telegram scene.
- Page now has a long Portal-like white editorial cadence: purpose → live route → source validation → watchpoints → travel scene → autonomous recovery → policy → arrival.
- Original project assets, not copied Portal material: `paper-route-v1.png`, `paper-recovery-v1.png`, existing plane scene, and original illustrated landscape.
- Final CTA lands into the illustrated meadow/mountain footer rather than ending on blank white.
- Desktop and mobile screenshots show no blank screen or clipped hero content.
- Mobile Telegram demo now uses the full mockup width (324px of a 390px viewport); the CTA has clear separation from the device rim.
- Mobile footer content is held on paper-white above the landscape; no text crosses the mountain edge.
- Production build passes; Sites packaging tests pass 4/4; browser story/asset/Telegram approval-replay test passes 1/1.

## Motion and regression evidence — 2026-08-24

- Browser coverage now includes desktop 1440×900, mobile 390×844, and `prefers-reduced-motion: reduce`.
- The flight and multimodal scenes use local frame assets, rAF-coalesced scroll sampling, and reduced-motion final-frame fallbacks.
- Visual capture command: `npm run test:visual` from `landing/`; output is kept outside git at `/private/tmp/trip-watch-qa/`.
- Release assertions cover hero framing, chapter cadence, white-background visual richness, film transition, final landscape, mobile overflow, reduced motion, console errors, asset failures, and Telegram approval/replay.

## Current verdict

Editorial composition and motion system: **passed final local regression gate**.
