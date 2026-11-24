<script lang="ts">
 export let name = 'server';
 export let size = 18;
 const paths: Record<string, string> = {
  server: 'M4 4h16v6H4z M4 14h16v6H4z M7 7h.01 M7 17h.01 M16 7h2 M16 17h2',
  cpu: 'M7 7h10v10H7z M10 10h4v4h-4z M9 3v4 M15 3v4 M9 17v4 M15 17v4 M3 9h4 M3 15h4 M17 9h4 M17 15h4',
  memory: 'M3 7h18v10H3z M6 10v4 M10 10v4 M14 10v4 M18 10v4 M6 17v3 M10 17v3 M14 17v3 M18 17v3',
  power: 'm13 2-9 12h7l-1 8 10-13h-7z',
  temperature: 'M9 14.5V5a3 3 0 0 1 6 0v9.5a5 5 0 1 1-6 0Z M12 10v8 M12 18h.01',
  fan: 'M12 12c-4-8 4-11 6-6 1 3-3 6-6 6Zm0 0c9 0 8 8 3 8-3 0-4-5-3-8Zm0 0c-5 7-11 2-8-2 2-3 6-1 8 2Z',
  disk: 'M5 4h14l3 11v5H2v-5z M2 15h20 M16 18h.01 M19 18h.01',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  chevron: 'm9 5 7 7-7 7',
  search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  refresh: 'M20 7V3l-3 3a8 8 0 1 0 3 9 M20 3h-4',
  warning: 'm12 3 10 18H2z M12 9v5 M12 17h.01',
  check: 'm5 12 4 4L19 6',
  close: 'm6 6 12 12 M6 18 18 6',
  moon: 'M20 15A8 8 0 0 1 9 4a9 9 0 1 0 11 11Z',
  sun: 'M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6 1.4 1.4 M17 17l1.4 1.4 M5.6 18.4 1.4-1.4 M17 7l1.4-1.4 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  copy: 'M9 9h11v11H9z M15 9V4H4v11h5',
  lock: 'M5 10h14v11H5z M8 10V6a4 4 0 0 1 8 0v4 M12 14v3',
  activity: 'M2 12h5l3-8 4 16 3-8h5',
 };
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d={paths[name] ?? paths.server} /></svg>
