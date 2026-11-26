export function createStore(reduce, initial) {
  let state = initial;
  const listeners = new Set();
  return {
    getState: () => state,
    dispatch(action) {
      const next = reduce(state, action);
      if (next !== state) {
        const previous = state;
        state = next;
        for (const listener of listeners) listener(next, previous, action);
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    }
  };
}
