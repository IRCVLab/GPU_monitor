# Cross-tool theme and storage tile polish

## Goal
Make GPU Monitor and Storage Monitor feel like one Clean product family while giving Storage's treemap the dominant share of the detail viewport.

## Approved direction
- Theme controls are 40px circular controls in both tools.
- Sun and moon SVGs remain mounted and crossfade with restrained rotation and scale; reduced motion switches immediately.
- The existing button-centered circular page reveal remains the mode-transition foundation.
- Storage detail keeps every mount and exact capacity fact, but changes six vertical capacity rows into a dense desktop capacity strip.
- Server identity and scan metadata use one compact header band.
- Tabs and the active mount controls lose redundant vertical margins.
- At 1280×720, the treemap should begin high enough to occupy roughly 65% or more of the visible page height.
- Tablet layouts wrap the capacity strip into three columns; narrow mobile layouts preserve readable one-column rows.
- No scanner, API, ordering, persistence, GPU live, or collection behavior changes.

## Interaction and accessibility
- Theme controls preserve accessible names, keyboard activation, and visible focus.
- Icon motion uses opacity, transform, and the existing native View Transition; no layout properties animate.
- Hover depth remains subtle and does not move adjacent content.
- `prefers-reduced-motion` disables icon and layout transition motion.

## Verification
- GPU frontend contract tests, Svelte checks, and build.
- Storage JavaScript and Python regression suites.
- Playwright screenshots in light and dark modes at 1280×720.
- Confirm Storage overview and detail routes, GPU dev, and all live service health responses.

