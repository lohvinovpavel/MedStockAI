"use client";

import { Suspense } from "react";
import { AnalogueWorkspace } from "@/components/AnalogueWorkspace";

// Owner: Pavlo. Backend: services/analogue (Ingress path /api/analogue).
// UC-1..5 live under the Analogues tab (DrugSearch + AnaloguesList).
// Physician cart demo lives under Prescribe. Session lives on the
// dashboard layout (no /auth bounce) so a physician cookie survives
// Analogues navigation and demo-login users are not kicked off the shell.
export default function AnaloguePage() {
  return (
    <Suspense fallback={<div className="p-4 text-xs text-muted-foreground">Loading…</div>}>
      <AnalogueWorkspace />
    </Suspense>
  );
}
