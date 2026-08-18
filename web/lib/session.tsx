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

// Build-time opt-in only — NEXT_PUBLIC_ vars are baked into the client
// bundle, so this can never be flipped on at runtime (a header, a query
// param) against a real deployment. Set it in web/.env.local when running
// against no backend/DB at all; leave it unset everywhere else. See
// .env.local.example.
export const LOCAL_AUTH_ENABLED = process.env.NEXT_PUBLIC_ALLOW_LOCAL_AUTH === "true";
const LOCAL_USER_KEY = "medstock-local-user";

function readLocalUser(): Me | null {
  if (!LOCAL_AUTH_ENABLED || typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(LOCAL_USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Me;
  } catch {
    return null;
  }
}

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
  // No-DB local dev only (see LOCAL_AUTH_ENABLED) — sets a client-side-only
  // session with no password and no backend call. A no-op when the flag is
  // off, so callers don't need to check it themselves.
  loginLocal: (role: string, email: string, fullName: string) => void;
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
    // A previously-established local session wins without a network round
    // trip — when there is no backend/DB, hitting /me is a doomed call on
    // every page load, not a real check.
    const local = readLocalUser();
    if (local) {
      setUser(local);
      return;
    }
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

  function loginLocal(role: string, email: string, fullName: string) {
    if (!LOCAL_AUTH_ENABLED) return;
    const localUser: Me = {
      user_id: `local-${role}`,
      email,
      full_name: fullName,
      role,
      hospital_id: "local-demo",
      // Shown wherever hospital_name renders, so a local-only session never
      // reads like a real signed-in facility.
      hospital_name: "Local Dev (no backend)",
    };
    window.localStorage.setItem(LOCAL_USER_KEY, JSON.stringify(localUser));
    setUser(localUser);
  }

  async function logout() {
    const wasLocal = readLocalUser() !== null;
    window.localStorage.removeItem(LOCAL_USER_KEY);
    if (wasLocal) {
      // Never backed by a real cookie — calling the real /logout here would
      // just be a guaranteed-failing request against a backend that isn't
      // there.
      setUser(null);
      return;
    }
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
    <SessionContext.Provider value={{ user, login, loginLocal, logout, refresh }}>
      {gated ? null : children}
    </SessionContext.Provider>
  );
}
