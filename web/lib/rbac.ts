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
 * which buttons on mock-data pages (orders/forecasts/shortages/audit —
 * lib/mock-data.ts, no backend yet) are offered. Hiding a button here is a
 * courtesy; a role with no cookie at all still can't reach anything real.
 */
export type Role = "pharmacist" | "physician" | "director" | "admin";

export const ROLE_LABEL: Record<Role, string> = {
  pharmacist: "Chief Pharmacist",
  admin: "Procurement Officer",
  director: "Clinical Director",
  physician: "Doctor",
};

// Page path -> roles allowed to land on it. Pages backed by a real service
// (inventory, analogue, warehouse) mirror that service's PERMS grant;
// orders/forecasts/shortages/audit have no backend yet, so this is the
// product call from docs/rbac-matrix.md.
const PAGE_ROLES: Record<string, Role[]> = {
  "/inventory": ["pharmacist", "physician", "director", "admin"],
  "/analogue": ["pharmacist", "physician", "director", "admin"],
  "/warehouse": ["pharmacist", "physician", "director", "admin"],
  "/shortages": ["pharmacist", "physician", "director", "admin"],
  "/forecasts": ["pharmacist", "director", "admin"],
  "/orders": ["pharmacist", "admin"],
  "/prognosis": ["pharmacist", "director", "admin"],
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

/** In-page action gates for the mock pages above — see docs/rbac-matrix.md. */
export const CAN: Record<string, Role[]> = {
  receiveBatch: ["pharmacist", "admin"],
  placeOrder: ["admin"],
  requestTransfer: ["pharmacist", "admin"],
  actOnForecast: ["pharmacist", "admin"],
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
