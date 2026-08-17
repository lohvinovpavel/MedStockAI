"use client";

import { useEffect, useState } from "react";

// Used where a component holds local state that must not be mounted twice
// (e.g. the copilot's message list) — CSS-only hide/show would create two
// divergent instances. Defaults to false (mobile-first) so SSR/first paint
// never assumes a viewport it hasn't measured yet.
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [query]);

  return matches;
}
