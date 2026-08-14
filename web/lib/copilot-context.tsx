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

type CopilotContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  focus: CopilotFocus;
  setFocus: (focus: CopilotFocus) => void;
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
  const toggle = useCallback(() => setOpen((o) => !o), []);

  return (
    <CopilotContext.Provider value={{ open, setOpen, toggle, focus, setFocus }}>
      {children}
    </CopilotContext.Provider>
  );
}
