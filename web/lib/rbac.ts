"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "@/lib/session";

/**
 * Frontend role gate.
 *
 * Mirrors PERMS in shared/medstock_shared/auth.py in spirit, not in code —
 * that file is the security boundary (every real endpoint 403s on its own,
 * see lib/prognosis.ts's approvalStance for the pattern). This one only
 * decides what the UI *shows*: which nav tabs and pages a role sees, and
 * which action buttons are offered. Hiding a button here is a courtesy;
 * every real endpoint still 403s on its own PERMS grant.
 */
export type Role = "pharmacist" | "physician" | "director" | "admin";

export const ROLE_LABEL: Record<Role, string> = {
  pharmacist: "Chief Pharmacist",
  admin: "Procurement Officer",
  director: "Clinical Director",
  physician: "Doctor",
};

// Pages backed by a real service mirror that service's PERMS grant.
const PAGE_ROLES: Record<string, Role[]> = {
  "/inventory": ["pharmacist", "physician", "director", "admin"],
  "/analogue": ["pharmacist", "physician", "director", "admin"],
  "/warehouse": ["pharmacist", "physician", "director", "admin"],
  "/shortages": ["pharmacist", "physician", "director", "admin"],
  // Mirrors forecast:read in shared PERMS — the prediction service is real
  // now (issue #7) and 403s admin on its own; the page follows the backend.
  "/forecasts": ["pharmacist", "director"],
  "/orders": ["pharmacist", "admin"],
  "/audit": ["pharmacist", "director", "admin"],
};

// Where a role lands after login, and where a denied page bounces it to.
export const HOME_ROUTE: Record<Role, string> = {
  pharmacist: "/inventory",
  admin: "/orders",
  director: "/audit",
  physician: "/analogue",
};

export function canAccessPage(role: string | undefined, path: string): boolean {
  const allowed = PAGE_ROLES[path];
  if (!allowed) return true; // unlisted route (e.g. /version) — open to any signed-in role
  return !!role && allowed.includes(role as Role);
}

/** In-page action gates for the pages above — see docs/rbac-matrix.md. Hiding a button is a courtesy; endpoints still 403. */
export const CAN: Record<string, Role[]> = {
  receiveBatch: ["pharmacist", "admin"],
  placeOrder: ["admin"],
  requestTransfer: ["pharmacist", "director"],
  actOnForecast: ["pharmacist", "admin"],
  // forecast:run in shared PERMS: triggering a forecast run (issue #7).
  runForecast: ["pharmacist", "director"],
  exportAudit: ["director"],
};

export function can(role: string | undefined, action: keyof typeof CAN): boolean {
  return !!role && CAN[action].includes(role as Role);
}

/**
 * Bounces a signed-in user off a page their role can't see — the "type the
 * URL directly" case. Returns true while the redirect is pending so the
 * caller can render nothing instead of flashing denied content.
 */
export function useRouteGuard(): boolean {
  const { user } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const denied = !!user && !canAccessPage(user.role, pathname);

  useEffect(() => {
    if (denied && user) router.replace(HOME_ROUTE[user.role as Role] ?? "/inventory");
  }, [denied, user, router]);

  return denied;
}
