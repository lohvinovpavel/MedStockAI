"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";

export type Me = {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  hospital_id: string;
  hospital_name: string;
};

// /version is deliberately public so it stays reachable when auth is down —
// it's the one page ops needs when login itself is the thing that's broken.
const PUBLIC_PATHS = ["/auth", "/version"];

// Only a same-app path is a safe redirect target — anything starting "//" or
// with a scheme is an open-redirect (e.g. //evil.com parses as protocol-
// relative). Shared by /auth (legacy) and /login (dashboard) so this guard
// exists in exactly one place rather than two copies drifting apart.
export function sanitizeNextPath(next: string | null): string {
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/";
}

type SessionContextValue = {
  user: Me | null | undefined; // undefined = still checking on first load
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used within a SessionProvider");
  return ctx;
}

export function SessionProvider({
  children,
  redirectToAuth = true,
  authPath = "/auth",
}: {
  children: React.ReactNode;
  redirectToAuth?: boolean;
  // Where a logged-out user is bounced. The legacy scaffold uses its bare
  // /auth form; the dashboard points this at /login instead, which is the
  // one styled sign-in page and shares the same backend cookie.
  authPath?: string;
}) {
  const [user, setUser] = useState<Me | null | undefined>(undefined);
  const pathname = usePathname();
  const router = useRouter();

  const refresh = useCallback(async () => {
    try {
      setUser(await apiFetch("auth", "/me"));
    } catch {
      // A 401 here is the normal logged-out state, not an error to log.
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auth 401s (not analogue/patients probes) funnel through this event so an
  // 8h token expiring mid-session clears the user without threading a
  // callback through every page.
  useEffect(() => {
    const onUnauthorized = () => setUser(null);
    window.addEventListener("medstock:unauthorized", onUnauthorized);
    return () => window.removeEventListener("medstock:unauthorized", onUnauthorized);
  }, []);

  useEffect(() => {
    if (!redirectToAuth) return;
    if (user !== null) return;
    if (pathname === authPath || PUBLIC_PATHS.includes(pathname)) return; // guards the auth page against redirecting to itself
    const qs = typeof window !== "undefined" ? window.location.search : "";
    router.replace(`${authPath}?next=${encodeURIComponent(`${pathname}${qs}`)}`);
  }, [user, pathname, router, redirectToAuth, authPath]);

  async function login(email: string, password: string) {
    await apiFetch("auth", "/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    // Built from a follow-up /me, not the login response — that response
    // lacks email/full_name/hospital_name.
    await refresh();
  }

  async function logout() {
    try {
      await apiFetch("auth", "/logout", { method: "POST" });
    } finally {
      // Clear local state even if the request throws — a network blip must
      // not leave the UI showing a logged-in user who isn't.
      setUser(null);
    }
  }

  const gated = redirectToAuth && user === undefined && pathname !== authPath && !PUBLIC_PATHS.includes(pathname);

  return (
    <SessionContext.Provider value={{ user, login, logout, refresh }}>
      {gated ? null : children}
    </SessionContext.Provider>
  );
}
