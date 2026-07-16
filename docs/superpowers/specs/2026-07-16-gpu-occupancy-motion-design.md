# GPU Occupancy Handoff Motion Design

## Goal

Make real GPU occupancy changes readable without delaying telemetry: idle-to-user, user-to-idle, and user-set changes should feel like a calm handoff rather than a hard DOM swap.

## Evidence

- `frontend/src/lib/components/GpuBar.svelte` replaces Full-view user/idle markup immediately and has no transition directive.
- `frontend/src/lib/components/CompactServerRow.svelte` also renders state/user content and tooltip ownership directly from unsorted `gpu.users`.
- `frontend/src/lib/styles/monitor-cards.css` animates metric width but not the Full-view G# state surface or user identity.
- `frontend/src/lib/styles/monitor-compact.css` changes Compact slot state surfaces and contents without transition or reduced-motion coverage.
- `backend/collectors/gpu.py` converts sets to lists without sorting, so unchanged membership can arrive in a different order and create false identity churn.
- `DESIGN.md` requires restrained 140–240ms motion and immediate reduced-motion fallback.

## Motion contract

- Telemetry data, percentages, memory values, ARIA text, and availability state update immediately.
- Only Full/Compact identity presentation and their G#/slot state surfaces animate.
- Identity changes use a keyed, height-stable presentation layer:
  - incoming: opacity `0 → 1`, translateY `2px → 0`, 220ms;
  - outgoing: opacity `1 → 0`, translateY `0 → -2px`, 160ms;
  - easing: Svelte `cubicOut`;
  - no scale, bounce, glow, blur, or stagger.
- G# background, border, text color, and hold collar settle over 240ms using the existing native-feeling easing curve.
- `prefers-reduced-motion: reduce` makes Svelte identity motion immediate and disables G# CSS transitions.
- Multiple usernames transition as one identity set. Existing Full and Compact wrapping behavior remains intact.
- Compact free/unknown/user content uses the same keyed signature and handoff timing; the slot surface changes border/background/color over 240ms.

## Stability contract

- Backend user lists are sorted deterministically before emission.
- Both `GpuBar` and `CompactServerRow` derive sorted display users so stale/mixed producers cannot create reorder-only animation; tooltip and ARIA ownership use the same order.
- GPU rows remain keyed by GPU index. Server and GPU ordering never changes.
- No timers, delayed store updates, new polling, dependencies, or layout animation.

## Acceptance

1. `idle → user`, `user → idle`, and `user A → user B` recreate only the keyed identity presentation.
2. G# visual state color transitions rather than snapping.
3. Metric fills keep their existing transition and values remain current.
4. A user-order-only payload change produces the same frontend identity signature.
5. Reduced-motion users receive an immediate swap.
6. Full and Compact layouts retain their existing geometry and no horizontal overflow is introduced.
7. Playwright intercepts DEV status responses with WebSocket disabled and exercises `idle → user`, `user → idle`, and `user A → user B`, asserting current values plus active identity/surface animations. Reduced-motion emulation asserts zero-duration identity motion and disabled CSS transitions.
