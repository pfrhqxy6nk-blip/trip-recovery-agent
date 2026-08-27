# 004 — Make visual QA a release gate

- **Status**: DONE
- **Commit**: bd9ca78
- **Severity**: MEDIUM
- **Category**: Performance / accessibility / validation
- **Estimated scope**: 2 files, ~120 lines

## Problem

The project had a real blank-screen regression caused by a missing React import, and the existing browser test only checks a desktop text string and one film frame:

```js
// landing/tests/browser-smoke.mjs:14-28 — current
await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" });
await page.locator("h1").waitFor({ state: "visible", timeout: 5000 });
const rootText = await page.locator("#root").innerText();
assert.match(rootText, /Your travel agent\s+lives in Telegram\./);
...
assert.match(activeFrame, /flight-film-0[2-3]\.png/);
```

It does not protect the eight-chapter story, mobile layout, approval interaction, final landscape, browser console, or missing image URLs.

## Target

Keep Playwright as the only browser dependency. Test at `1440×900` and `390×844`. Add assertions for: all chapter IDs exist; exactly four film images load; the final CTA background image resolves with natural width above zero; the approval/replay flow works; no `console.error` occurs; and the mobile document has no element wider than `window.innerWidth + 1`.

Capture approved QA images outside the repository in `/private/tmp/trip-watch-qa/`: `desktop-hero.png`, `desktop-watch.png`, `desktop-film.png`, `desktop-arrival.png`, `mobile-hero.png`, and `mobile-arrival.png`. Update `landing/design-qa.md` after each visual review with date, tested breakpoints, passed checks, and remaining issues.

## Repo conventions to follow

- Browser tests live in `landing/tests/browser-smoke.mjs` and run through `npm run test:browser` in `landing/package.json`.
- Packaging checks live in `landing/tests/sites-worker.test.mjs`; do not edit them.
- The front end must work on a local Vite server at `http://127.0.0.1:5173/`.

## Steps

1. Refactor `landing/tests/browser-smoke.mjs` into focused tests for desktop story, Telegram approval/replay, film progression, and mobile overflow. Continue using `try/finally` to close the browser.
2. In each test, collect console errors and failed network requests for `/assets/`; assert both collections are empty after the page settles.
3. Create a manual screenshot script under `landing/tests/` that captures the six named files outside git. It must not change application source or request external URLs.
4. Add an exact command to `landing/package.json`, `test:visual`, to run this capture script locally after the dev server is already running.
5. Update `landing/design-qa.md` with a compact checklist: hero framing, chapter cadence, white-background visual richness, film transition, final landscape, mobile overflow, reduced motion, and functional Telegram approval.

## Boundaries

- Do NOT add screenshot snapshots to git unless the user explicitly asks for committed baselines.
- Do NOT launch deployment, publish the site, or test a remote endpoint.
- Do NOT weaken a failing assertion merely to make a test pass; fix the selector or the product behavior.

## Verification

- **Mechanical**: run `npm run build`, `npm run test:sites`, `npm run test:browser`, and `npm run test:visual`; all must exit 0.
- **Feel check**: compare the six screenshots beside the supplied Portal reference. Confirm the page begins with a framed twilight product world, breathes through rich white editorial scenes, has a contained film midpoint, and ends by landing in the illustrated landscape.
- **Done when**: an import failure, missing asset, broken approval button, desktop/mobile overflow, or missing final visual causes a local test or QA check to fail before a user sees it.

## Delivery evidence — 2026-08-24

- `landing/tests/browser-smoke.mjs` now covers desktop story selectors, asset loading, film progression, Telegram approval/replay, mobile overflow, reduced motion, console errors, and asset request failures.
- `landing/tests/capture-visual.mjs` captures the six approved QA views outside git in `/private/tmp/trip-watch-qa/`.
- Full local browser and visual commands pass on this checkout; the gate is ready to run before each visual release.
