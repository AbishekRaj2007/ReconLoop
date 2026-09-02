import { useEffect, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetches `fetcher()` on mount and whenever `deps` change. Pass `null` for
 * `fetcher` to skip fetching (e.g. while a dependency like a run id isn't
 * known yet) — state stays in `loading` until a non-null fetcher is given.
 * Returns a `reload` function to re-run the same fetch on demand.
 */
export function useAsyncResource<T>(fetcher: (() => Promise<T>) | null, deps: unknown[] = []) {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!fetcher) return;
    Promise.resolve()
      .then(() => {
        setState((prev) => ({ ...prev, loading: true, error: null }));
        return fetcher();
      })
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((err) =>
        setState({
          data: null,
          error: err instanceof Error ? err.message : "Request failed",
          loading: false,
        })
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadToken]);

  return { ...state, reload: () => setReloadToken((n) => n + 1) };
}
