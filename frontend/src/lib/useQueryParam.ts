"use client";

import { useEffect, useState } from "react";

/** Read a query parameter once on mount (avoids a Suspense boundary). */
export function useQueryParam(name: string): string | null {
  const [v, setV] = useState<string | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read once after mount (no Suspense boundary needed)
    setV(new URLSearchParams(window.location.search).get(name));
  }, [name]);
  return v;
}
