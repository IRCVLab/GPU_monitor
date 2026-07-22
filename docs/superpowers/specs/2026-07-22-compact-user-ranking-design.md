# Compact User Ranking Design

## Goal

Replace the oversized Users chart and duplicate table with one dense, legible ranking surface. A researcher should be able to answer, without scanning two separate representations:

1. who is using the most storage;
2. how much each user occupies relative to the top user;
3. which local mounts make up that usage;
4. the exact usage, share, and file count;
5. the selected user's per-mount breakdown.

The change must preserve the current server snapshot, mount-filter, sorting, and accessibility semantics. It must not change collection, aggregation, or capacity calculations.

## Current Problem

The Users tab renders a stacked ECharts bar chart up to roughly 570 px tall, then repeats the same ranking in a table. On real Hinton data this produces thin bars, a large mostly empty plotting field after the largest outlier, duplicated mount labels, and a second scroll-heavy representation below it. The visual hierarchy favors chart scaffolding instead of user identity and exact usage.

## Approved Direction

Use a single compact ranking table. Each user occupies one approximately 32 px row with these columns:

1. **User** — prominent full username with subdued UID.
2. **Distribution** — one segmented horizontal track.
3. **Usage** — exact scoped usage.
4. **Share** — percentage of the selected storage scope.
5. **Files** — file count.

The default order remains usage descending.

## Distribution Encoding

The distribution track conveys total scale and mount composition simultaneously:

- the complete colored length is proportional to `userScopeBytes(user) / maxScopedUserBytes`;
- each colored segment within that length is proportional to the user's bytes on that mount;
- the unused remainder stays as a quiet semantic track;
- mount colors continue to come from the existing `mountColor` mapping;
- when one mount is selected, the row uses only that mount's color and remains scaled against the largest user in that filtered scope;
- exact values remain in text, so color is never the sole communication channel.

This preserves the useful visual cue from the chart without axes, plotting whitespace, or a second representation.

## Mount Filter

Keep the existing `All` and per-mount segmented control above the ranking. Remove the separate legend row because it duplicates the same mount names.

Each per-mount filter button receives its existing mount-color dot. `All` remains neutral. Changing the filter recomputes row order, distribution widths, usage, share, file totals, and the user count using the existing scope helpers.

## Expanded User Detail

Selecting a row inserts one detail row directly below it. Only one user can be expanded at a time.

The expanded row shows non-zero mounts in descending byte order with:

- mount path;
- exact bytes;
- percentage of that user's total usage;
- a compact proportional bar.

Selecting the open row closes it. Keyboard `Enter` and `Space` provide the same behavior. The expansion must not scroll the user to a second component because the second component no longer exists.

## Layout and Density

- Use one rounded card matching the existing Storage Monitor card material.
- Keep a sticky table header.
- Use tabular numerals for all numeric columns.
- Use a viewport-bounded internal scroll region only when the full user list exceeds available height.
- Target approximately 32 px per primary row and compact expanded content.
- At narrow widths, retain User, Distribution, and Usage; hide Share and Files before allowing horizontal overflow.
- Preserve visible focus and a minimum practical row target without inflating desktop density.

## Data and State Flow

1. `renderUsers()` reads `DATA.users` and the current `userMountFilter`.
2. It derives scoped bytes with the existing `userScopeBytes` helper.
3. It sorts using the existing `usersSort` state.
4. It computes the maximum scoped user value for visual normalization.
5. It renders table rows and segmented distribution tracks.
6. Filter and sort changes rerender the same surface.
7. Server changes reset the filter and expanded user state through the existing detail render lifecycle.

No new API fields, dependencies, remote assets, or collection behavior are required.

## Removal and Simplification

- Remove the Users-specific ECharts rendering path and fixed `#usersChart` height.
- Remove the duplicate mount legend.
- Remove chart-click-to-scroll behavior.
- Retain ECharts only where still required by other views.
- Reuse current user sorting, scope calculation, row expansion, formatting, and mount-color utilities.

## Error and Empty States

- If no users exist in the selected scope, render one compact empty row with the existing no-data meaning.
- Missing per-mount data leaves the distribution track empty but keeps textual totals visible.
- Invalid or zero capacity uses a safe denominator and never emits `NaN` or infinite widths.
- A server transition must hide the previous server detail until the new snapshot is ready, preserving the server-switch fix.

## Accessibility

- Keep a semantic table with sortable column buttons/headers.
- Rows remain keyboard focusable and expose expansion state through `aria-expanded`.
- The expanded row is associated with its owner row.
- Mount colors are reinforced by path labels and exact values.
- Focus-visible styling remains obvious in light and dark themes.
- Reduced-motion users receive no scroll or expansion animation requirement.

## Verification

Automated regression coverage must prove:

1. usage-descending default order;
2. distribution total width relative to the largest scoped user;
3. segment widths and mount colors reflect `by_mount` values;
4. mount filtering recomputes order and scale;
5. exact usage/share/files remain correct;
6. only one row expands and keyboard controls work;
7. no Users chart or duplicate legend remains;
8. responsive columns do not create horizontal overflow;
9. switching servers cannot expose the previous server's Users content;
10. existing Python, viewer, data, and collector suites remain green.

Playwright verification must compare at least two real servers, exercise `All` plus one mount filter, open and close one user detail row, and report console errors.
