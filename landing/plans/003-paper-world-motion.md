# 003 — Animate the white chapters like editorial travel frames

- **Status**: DONE
- **Commit**: bd9ca78
- **Severity**: MEDIUM
- **Category**: Missed opportunities / cohesion
- **Estimated scope**: 3 files, ~180 lines plus assets from plan 001

## Problem

Outside the flight block, the page has no deliberate entrance choreography. The current white chapters are static, and the only micro-motion is a generic button hover:

```css
/* landing/src/styles.css:23-24 — current */
.hero-cta, .blue-cta { transition: transform .2s ease, box-shadow .2s ease; }
.hero-cta:hover, .blue-cta:hover { transform: translateY(-2px); box-shadow: 0 7px 20px rgba(0,0,0,.19); }
```

This is insufficient for the requested film quality and makes the page jump from static paper to a full cinematic landscape.

## Target

After plan 001 has introduced the editorial chapters and raster assets, add a small reusable reveal system. Elements begin with `opacity: 0; transform: translateY(18px);` and receive `is-visible` via `IntersectionObserver` once, with:

```css
transition: opacity 700ms var(--ease-out), transform 700ms var(--ease-out);
```

Within each scene, permit at most three sequential elements with `80ms` delays. Decorative asset layers may use `transform: translate3d(0, 12px, 0) rotate(-1.5deg)` initially and settle to `translate3d(0, 0, 0) rotate(0)` over `900ms var(--ease-out)`. The recovery confirmation stack may reveal one card every `120ms`, but only once when it enters view. CTA press feedback uses `transform: scale(.97)` for `160ms var(--ease-out)`. Never animate every paragraph, every list row, or a hovering loop.

For reduced motion, retain opacity at `200ms var(--ease-out)` and remove all transform/rotation/stagger delays.

## Repo conventions to follow

- Add all easing to the root token declaration in `landing/src/styles.css:3`; plan 002 defines `--ease-out`.
- The existing CTA class hooks are `landing/src/styles.css:23-24`.
- Interactive state is isolated in `landing/src/App.jsx:6-18`; do not make the Telegram recovery interaction dependent on scrolling.

## Steps

1. After plan 001 is implemented, add `data-reveal` attributes only to chapter heading groups and their single visual anchor. Do not mark every text child.
2. Add a `useEffect` in `landing/src/App.jsx` that observes `[data-reveal]` with `{ threshold: 0.18 }`, adds `is-visible` once, and disconnects on cleanup. Respect reduced motion through `window.matchMedia("(prefers-reduced-motion: reduce)")` by marking elements visible immediately.
3. Add `.reveal`/`.is-visible` styles using the exact values above. Apply stagger with CSS custom property `--reveal-delay`; values may only be `0ms`, `80ms`, or `160ms`.
4. Replace the current CTA hover with `transform 200ms var(--ease-out), box-shadow 200ms var(--ease-out)` and add `:active { transform: scale(.97); transition-duration: 160ms; }`. Keep the shadow static on white paper; it must not pulse or animate endlessly.
5. For the rare Telegram approval confirmation, use a one-time opacity/translate transition for the success bubble; do not show a loading spinner or bounce animation for a consequential financial decision.
6. Extend the browser test to click `Approve recovery`, assert `Trip recovered.`, click `Replay demo`, and assert the approval prompt returns.

## Boundaries

- Do NOT use a scroll library, animation library, or continuous parallax loop.
- Do NOT animate functional navigation, the menu, or high-frequency watch rows.
- Do NOT use blur-in or large scale-from-zero effects.
- Do NOT hide content from keyboard or screen-reader users while waiting for IntersectionObserver.

## Verification

- **Mechanical**: run `npm run build`, `npm run test:sites`, and `npm run test:browser`; all must pass.
- **Feel check**: set browser animation playback to 10%. Confirm each white scene enters as one composed editorial frame, not a cascade of list items. Confirm rapid scroll past a scene does not replay it.
- **Accessibility**: emulate reduced motion and verify the page stays readable with opacity-only transition and no stagger delay.
- **Done when**: motion adds direction between paper scenes without competing with the plane film or the Telegram interaction.

## Delivery evidence — 2026-08-24

- Editorial chapter headings and visual anchors use one-shot `data-reveal` IntersectionObserver choreography.
- Stagger values are limited to the documented 0/80/160ms tokens, with opacity-only reduced-motion behavior.
- Telegram approval remains independent from scroll and reveal state.
