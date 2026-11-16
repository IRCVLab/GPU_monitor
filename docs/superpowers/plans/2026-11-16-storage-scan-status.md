# Storage Scan Status Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development with test-first changes and independent review.

**Goal:** Make scan progress, completion, schedule and outcomes visible, with a stable Rescan control.

**Architecture:** Independent bounded progress JSON from scanner/agent, fixed-path SSH collection and authenticated status endpoint; separate viewer status module and navigation-safe polling. Preserve last-good snapshot and existing rescan security.

**Tech Stack:** C scanner, Python agent/collector/HTTP API, vanilla JavaScript and CSS.

## Task 1: Agent and central telemetry
- [x] Add failing tests for running/terminal telemetry, bounded validation, old-agent fallback, timer times and polling during rescan.
- [x] Implement scanner progress, agent lifecycle, transport collection, central status and API route. Own scanner/, agent/, collector/, viewer/serve.py and viewer/test_serve.py.
- [x] Run Python and scanner tests. Document the remote agent upgrade requirement.

## Task 2: Viewer
- [x] Add failing behavior tests for completion timestamp, ETA unavailable/overrun, unavailable schedule and state rendering.
- [x] Add scan-status.js, panel markup and styles; remove orbit and narrow-screen icon-only Rescan treatment.
- [x] Poll status for detail view with navigation guards, refresh data when a new completed generation appears, and retain existing rescan error behavior.
- [x] Run viewer tests and inspect local sample preview at desktop and mobile widths.

## Task 3: Integration and review
- [x] Review spec compliance then implementation quality; resolve findings.
- [ ] Run full storage checks, repository contracts and diff check.
- [x] Leave a reviewable local branch and preview, report deployment requirements accurately.

## Validation notes

- Independent frontend and backend reviews completed; corrected asynchronous refresh blocking, stale telemetry presentation, history/ETA mixing, and release packaging.
- Browser QA passed at 320, 390, 820 and 1440 pixels in light/dark themes; stable Rescan dimensions and no horizontal page overflow or JavaScript errors.
- Use Python 3.12 with pytest and `umask 022` for repository verification. Existing deployment fixtures assume non-group-writable directories; the host default umask 0002 reproduces eight failures on the unchanged baseline.
- Preserve this local feature branch and preview for review. Production central and remote agent deployment has not been performed.
