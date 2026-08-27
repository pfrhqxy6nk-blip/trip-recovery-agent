# Trip Watch visual and motion plan

| # | Plan | Severity | Status |
|---|---|---|---|
| 001 | Rebuild the page as a white-paper travel story | HIGH | DONE |
| 002 | Make the flight sequence a precise, accessible scroll scene | HIGH | DONE |
| 003 | Animate the white chapters like editorial travel frames | MEDIUM | DONE |
| 004 | Make visual QA a release gate | MEDIUM | DONE |

## Recommended order

1. Execute **001** first. It establishes the required story chapters, generated paper-world assets, and final landing image.
2. Execute **002** second. It turns the existing plane assets into a short, performant midpoint between chapters rather than a page-ending dark block.
3. Execute **003** third. It adds restrained entrance choreography to the finished scenes.
4. Execute **004** last, then run it after every visual change.

## Dependencies

- 002 can be implemented independently, but it must be placed between the trust and recovery chapters introduced by 001.
- 003 depends on the semantic sections and assets in 001 and the easing tokens in 002.
- 004 should validate the final page after 001–003; it uses the existing local Vite workflow and adds no deployment work.

## Delivery status — 2026-08-24

- 001 is implemented in `landing/src/App.jsx` and `landing/src/styles.css` with the eight-chapter editorial story and original local assets.
- 002 is implemented with requestAnimationFrame scroll sampling, local frame preloading, compositor-only transitions, and reduced-motion final-frame behavior.
- 003 is implemented with one-shot IntersectionObserver reveals, restrained stagger tokens, and reduced-motion fallbacks.
- 004 is complete: the desktop/mobile/reduced-motion browser suite and visual capture command pass on the current checkout.
