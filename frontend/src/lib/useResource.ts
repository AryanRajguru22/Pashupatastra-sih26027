"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch on mount, when `deps` change, and whenever a mutation bumps the
 * session tick. No optimistic updates: screens always show the server's answer.
 */
export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  enabled = true,
): Resource<T> {
  const { tick } = useSession();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [local, setLocal] = useState(0);
  const seq = useRef(0);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    // Standard fetch-on-change pattern: state is set when the request starts
    // and again when it settles.
    if (!enabled) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false);
      return;
    }
    const mine = ++seq.current;
    setLoading(true);
    fetcherRef
      .current()
      .then((d) => {
        if (mine !== seq.current) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (mine !== seq.current) return;
        setError(
          e instanceof ApiError
            ? e
            : new ApiError(0, "UNKNOWN", e instanceof Error ? e.message : "Unknown error"),
        );
      })
      .finally(() => {
        if (mine === seq.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, local, enabled, ...deps]);

  const reload = useCallback(() => setLocal((n) => n + 1), []);
  return { data, error, loading, reload };
}
