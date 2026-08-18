"use client";

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";

// Persists the collapsed/open choice — a user who opens the copilot once
// shouldn't have to reopen it on every navigation, and one who never
// touches it shouldn't have it stealing table width from every page (see
// UX-10: at 1280px it left the inventory table narrower than its own columns).
const OPEN_STORAGE_KEY = "medstock-copilot-open";

// What the copilot drawer is currently "looking at" — set by whichever
// dashboard page the user is on (a selected SKU row, a shortage alert, …).
// Kept as a small tagged union instead of `unknown` so the drawer can render
// a sensible summary card without guessing the shape.
// `itemId` lets the drawer read the real inventory row for its quick-action
// replies (analogue/certificate/PO) instead of returning canned data that
// ignores whatever SKU is actually focused.
export type CopilotFocus =
  | { kind: "sku"; label: string; detail: string; itemId: string; ndc?: string; rxcui?: string | null }
  | { kind: "alert"; label: string; detail: string; ndc?: string }
  | { kind: "patient"; label: string; detail: string; patientId: string; rxcui?: string; drugName?: string }
  | null;

// A one-shot "do this now" ask fired by a page (e.g. the forecast scenario
// simulator's emergency-plan button). `nonce` makes repeat clicks with the
// same params distinguishable so the drawer's effect re-fires each time.
export type EmergencyPlanRequest = {
  drugName: string;
  surgePct: number;
  depletionDays: number | null;
  nonce: number;
};

type CopilotContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  focus: CopilotFocus;
  setFocus: (focus: CopilotFocus) => void;
  emergencyRequest: EmergencyPlanRequest | null;
  requestEmergencyPlan: (req: Omit<EmergencyPlanRequest, "nonce">) => void;
};

const CopilotContext = createContext<CopilotContextValue | null>(null);

export function useCopilot() {
  const ctx = useContext(CopilotContext);
  if (!ctx) throw new Error("useCopilot must be used within a CopilotProvider");
  return ctx;
}

export function CopilotProvider({ children }: { children: ReactNode }) {
  // Defaults closed — corrected from localStorage in an effect (not read
  // synchronously) so server and first-client render agree and React
  // doesn't flag a hydration mismatch.
  const [open, setOpenState] = useState(false);
  useEffect(() => {
    const stored = window.localStorage.getItem(OPEN_STORAGE_KEY);
    if (stored !== null) setOpenState(stored === "true");
  }, []);
  const setOpen = useCallback((next: boolean) => {
    setOpenState(next);
    window.localStorage.setItem(OPEN_STORAGE_KEY, String(next));
  }, []);
  const [focus, setFocus] = useState<CopilotFocus>(null);
  const [emergencyRequest, setEmergencyRequest] = useState<EmergencyPlanRequest | null>(null);
  const toggle = useCallback(() => setOpen(!open), [open, setOpen]);
  const requestEmergencyPlan = useCallback((req: Omit<EmergencyPlanRequest, "nonce">) => {
    setOpen(true);
    setEmergencyRequest({ ...req, nonce: Date.now() });
  }, [setOpen]);

  return (
    <CopilotContext.Provider value={{ open, setOpen, toggle, focus, setFocus, emergencyRequest, requestEmergencyPlan }}>
      {children}
    </CopilotContext.Provider>
  );
}
