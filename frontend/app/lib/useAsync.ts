import { useCallback, useEffect, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useAsyncResource<T>(fetcher: () => Promise<T>) {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });

  const reload = useCallback(() => {
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
  }, []);

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { ...state, reload };
}
