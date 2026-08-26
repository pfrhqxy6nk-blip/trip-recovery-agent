# 002 — Make the flight sequence a precise, accessible scroll scene

- **Status**: DONE
- **Commit**: bd9ca78
- **Severity**: HIGH
- **Category**: Performance / accessibility / easing
- **Estimated scope**: 3 files, ~100 lines

## Problem

`landing/src/App.jsx:25-35` calls React state on every native scroll event. `landing/src/styles.css:46-59` turns the scene into a 400vh dark block, uses raw cubic-bezier values rather than shared tokens, and uses default `ease` in the progress and reduced-motion modes.

```jsx
// landing/src/App.jsx:25-34 — current
const updateFilm = () => {
  const film = document.getElementById("flight-film");
  if (!film) return;
  const top = film.getBoundingClientRect().top;
  const span = Math.max(1, film.offsetHeight - window.innerHeight);
  setFilmFrame(Math.max(0, Math.min(3, Math.floor(((window.innerHeight - top) / span) * 4))));
};
updateFilm(); window.addEventListener("scroll", updateFilm, { passive: true });
```

```css
/* landing/src/styles.css:46-58 — current */
.flight-film { height: 400vh; position: relative; background: #0c277b; }
.film-frame { transition: opacity .8s cubic-bezier(.23,1,.32,1), transform 1.2s cubic-bezier(.23,1,.32,1); }
.film-progress i { transition: background .3s ease, transform .3s ease; }
```

## Target

Add these tokens to `landing/src/styles.css:3`:

```css
--ease-out: cubic-bezier(0.23, 1, 0.32, 1);
--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
```

The scene remains an intentional 4-frame story on desktop at `320vh`, and `240vh` on screens at or below `760px`. Each change uses only compositor-friendly properties:

```css
.film-frame {
  opacity: 0;
  transform: scale(1.025);
  transition: opacity 800ms var(--ease-out), transform 1200ms var(--ease-out);
}
.film-frame.active { opacity: 1; transform: scale(1); }
.film-progress i { transition: background-color 300ms var(--ease-out), transform 300ms var(--ease-out); }
```

Use one `requestAnimationFrame` queue from scroll/resize, calculate the next integer frame, and call `setFilmFrame` only if the index differs from the currently rendered index. Preload the four local images once with `new Image().src` during the effect. For `prefers-reduced-motion: reduce`, remove the sticky multi-scroll experience: use `height: auto`, show only the final frame, and animate opacity at `200ms var(--ease-out)`; no scale or progress animation.

## Repo conventions to follow

- React state/effect lives in `landing/src/App.jsx:20-35`.
- The four assets are already rendered at `landing/src/App.jsx:45`.
- CSS transitions already target transform/opacity at `landing/src/styles.css:49`; preserve that property discipline.

## Steps

1. Add the exact easing tokens to the existing `:root` declaration in `landing/src/styles.css:3` and replace every raw `ease` in the film and primary CTA motion with the matching token; do not use `transition: all`.
2. In `landing/src/App.jsx`, replace the scroll handler with a `requestAnimationFrame`-throttled calculation. Store last frame in a ref, clean up the scroll and resize listeners, cancel an outstanding animation frame, and only call state when the frame changes.
3. Preload `/assets/flight-film-01.png` through `04.png` once in the same effect. Keep the image tags in the DOM to avoid a flash when frame 2 begins.
4. Change film heights to `320vh` desktop and `240vh` mobile; keep it between the trust and recovery chapters from plan 001, never as the final section.
5. Implement the exact reduced-motion behavior above and retain a meaningful final narrative for screen readers through the existing text.
6. Update `landing/tests/browser-smoke.mjs` to verify a second frame after one scroll and a final frame near the end of the film, without asserting transition timing.

## Boundaries

- Do NOT add GSAP, Framer Motion, Lenis, a canvas, video, or a new dependency.
- Do NOT animate layout properties, filter, box-shadow, or background-position in the scroll handler.
- Do NOT turn scroll into forced snap scrolling.
- Do NOT change the four frame images themselves in this plan.

## Verification

- **Mechanical**: run `npm run build`, `npm run test:sites`, and `npm run test:browser`; all must pass.
- **Feel check**: with desktop devtools throttled to 4× CPU, scroll from frame 1 to 4, reverse direction mid-crossfade, and confirm the image change stays calm with no lag or visual flash.
- **Accessibility**: emulate `prefers-reduced-motion: reduce`; confirm the scene is not sticky, only its final image is visible, and its copy remains readable.
- **Done when**: Chrome Performance shows no repeated React updates for unchanged frames, no layout-shifting animation property is used, and the four-frame sequence resolves before the white recovery chapter.

## Delivery evidence — 2026-08-24

- Scroll and resize events are coalesced through one requestAnimationFrame queue and state updates are skipped when the frame index is unchanged.
- Flight frames are preloaded locally; reduced motion uses the final frame without sticky multi-scroll behavior.
- The browser suite checks frame progression, final-frame resolution, and reduced-motion layout semantics.
