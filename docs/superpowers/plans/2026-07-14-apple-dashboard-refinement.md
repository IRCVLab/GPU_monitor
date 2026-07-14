> Superseded by /DESIGN.md after the UX reset on 2026-07-14. Do not implement this narrow refinement as the current design.

# Dashboard Refinement Implementation Plan

1. Add a no-dependency masonry measurement action and apply it to dashboard card wrappers.
2. Pass filter context into `ServerCard` and simplify card-header metadata and edit affordance.
3. Refine `GpuBar` markup and theme-aware metric styling.
4. Unify card surface/divider styling and add reduced-motion-safe microinteractions.
5. Run diagnostics/build, inspect dark/light and responsive screenshots, fix regressions.
6. Commit the verified feature branch implementation.
