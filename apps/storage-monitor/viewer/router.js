const tabs = new Set(['treemap','users','top','stale']);
export function parseUrl(input) {
  const url = new URL(input, 'http://viewer.local');
  const query = url.searchParams;
  const serverId = query.get('server');
  if (!serverId || !/^[\w.-]+$/.test(serverId) || ['.','..'].includes(serverId))
    return {page:query.get('view') === 'statistics' ? 'statistics' : 'overview'};
  const requested = query.get('tab') || url.hash.slice(1);
  const tab = requested === 'topfiles' ? 'top' : tabs.has(requested) ? requested : 'treemap';
  const owner = query.get('owner');
  return {page:'server', serverId, tab, volumeId:query.get('vol'), path:(query.get('path') || '').split('/').filter(Boolean),
    owner:owner !== null && /^\d+$/.test(owner) ? Number(owner) : null, mount:query.get('mount')};
}
export function formatUrl(route) {
  if (route.page !== 'server') return route.page === 'statistics' ? '/?view=statistics' : '/';
  const q = new URLSearchParams({server:route.serverId, tab:route.tab});
  if (route.volumeId) q.set('vol',route.volumeId);
  if (route.path?.length) q.set('path',route.path.join('/'));
  if (route.owner != null) q.set('owner',route.owner);
  if (route.mount) q.set('mount',route.mount);
  return '/?' + q;
}
export function bindRouter(store) {
  const navigate = (route, replace = false) => {
    const href = formatUrl(route);
    if (href !== location.pathname + location.search + location.hash) {
      history[replace ? 'replaceState' : 'pushState'](null, '', href);
    }
    store.dispatch({type:'Navigate',route:parseUrl(location.href)});
  };
  window.addEventListener('popstate', () => store.dispatch({type:'PopState',route:parseUrl(location.href)}));
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || link.origin !== location.origin || link.pathname !== location.pathname ||
        event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(parseUrl(link.href));
  });
  return navigate;
}
