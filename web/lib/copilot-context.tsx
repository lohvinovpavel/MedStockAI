"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

// What the copilot drawer is currently "looking at" — set by whichever
// dashboard page the user is on (a selected SKU row, a shortage alert, …).
// Kept as a small tagged union instead of `unknown` so the drawer can render
// a sensible summary card without guessing the shape.
export type CopilotFocus =
  | { kind: "sku"; label: string; detail: string }
  | { kind: "alert"; label: string; detail: string }
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
  const [open, setOpen] = useState(true);
  const [focus, setFocus] = useState<CopilotFocus>(null);
  const [emergencyRequest, setEmergencyRequest] = useState<EmergencyPlanRequest | null>(null);
  const toggle = useCallback(() => setOpen((o) => !o), []);
  const requestEmergencyPlan = useCallback((req: Omit<EmergencyPlanRequest, "nonce">) => {
    setOpen(true);
    setEmergencyRequest({ ...req, nonce: Date.now() });
  }, []);

  return (
    <CopilotContext.Provider value={{ open, setOpen, toggle, focus, setFocus, emergencyRequest, requestEmergencyPlan }}>
      {children}
    </CopilotContext.Provider>
  );
}
