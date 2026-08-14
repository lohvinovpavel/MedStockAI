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

export function SessionProvider({ children }: { children: React.ReactNode }) {
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

  // Every apiFetch call funnels 401s through this one event, so an 8h token
  // expiring mid-session on any page logs the user out without threading a
  // callback through every page.
  useEffect(() => {
    const onUnauthorized = () => setUser(null);
    window.addEventListener("medstock:unauthorized", onUnauthorized);
    return () => window.removeEventListener("medstock:unauthorized", onUnauthorized);
  }, []);

  useEffect(() => {
    if (user !== null) return;
    if (PUBLIC_PATHS.includes(pathname)) return; // guards /auth against redirecting to itself
    router.replace(`/auth?next=${encodeURIComponent(pathname)}`);
  }, [user, pathname, router]);

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

  const gated = user === undefined && !PUBLIC_PATHS.includes(pathname);

  return (
    <SessionContext.Provider value={{ user, login, logout, refresh }}>
      {gated ? null : children}
    </SessionContext.Provider>
  );
}
