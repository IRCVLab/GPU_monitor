# Monitor Card Header Contract

GPU Monitor and Storage Monitor are separate applications, but their server cards must read as one product family. They share this header contract rather than importing a runtime component across the Svelte and vanilla JavaScript stacks.

## Anatomy

1. One continuous card surface; the header is not a separate tinted cap.
2. One title row containing:
   - server name
   - compact status dot
   - one quiet monospace metadata value
3. Healthy state is communicated by the dot only. Text is reserved for actionable warning or failure states inline after the dot, matching GPU Monitor's status anatomy.
4. Product-specific metadata is allowed: GPU Monitor uses the endpoint; Storage Monitor uses the local mount count.

## Fixed rendered geometry

These values mirror the computed GPU Monitor dev card header and are intentionally expressed in pixels so a different root font size cannot silently change the product template.

| Token | Value |
| --- | ---: |
| Card radius | `24px` |
| Header padding | `9px 12px 8px` |
| Title-row minimum height | `30px` |
| Title-row gap | `8px` |
| Title-line gap | `6px` |
| Title size / line height | `15.2px / 19.76px` |
| Status dot | `8px` |
| Secondary metadata | `9.76px / 1` |

## Surface and hierarchy

- The header background remains transparent so the card reads as one object.
- No header-only bottom border is used.
- Server name uses the primary foreground at 94% strength.
- Metadata uses the primary foreground at 38% strength.
- Mount rows retain their current information density; this contract changes only server-card identity and spacing.

## Responsive behavior

- The title line may shrink and clip metadata, but the server name keeps first priority.
- Actionable state text stays on one line and never pushes the card wider than its grid column.
- The contract is identical at one-, two-, and three-column breakpoints.

## Verification

- Static regression tests assert the DOM anatomy and semantic tokens.
- Pre-deploy Playwright verification compares Storage computed styles against GPU Monitor dev at the same viewport width. It must check the selectors below rather than relying on screenshots alone:
  - Storage: `.overview-card`, `.overview-card-header`, `.overview-card-title-row`, `.overview-card-title-line`, `.overview-name`, `.overview-status-dot`, `.overview-meta`
  - GPU dev: `.monitor-card`, `.monitor-card__header`, `.monitor-card__title-row`, `.monitor-card__title-line`, `.monitor-card__title`, `.monitor-card__status-dot`, `.monitor-card__host`
  - Expected Storage computed values: `24px` radius, `47px` header height, `9px 12px 8px` padding, static header position, no header border, transparent header background, `15.2px / 19.76px` title, `8px` dot, and `9.76px` metadata.
- The same Playwright pass checks `document.documentElement.scrollWidth === document.documentElement.clientWidth` at a `390px` viewport.
- Storage deployment must not restart or modify GPU Monitor live or dev services.
