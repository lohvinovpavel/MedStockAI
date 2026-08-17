"use client";

import { Suspense } from "react";
import { SessionProvider } from "@/lib/session";
import { AnalogueWorkspace } from "@/components/AnalogueWorkspace";

// Owner: Pavlo. Backend: services/analogue (Ingress path /api/analogue).
// UC-1..5 live under the "Пошук аналогів" tab (DrugSearch + AnaloguesList).
// Physician cart demo lives under "Призначення". Session is scoped here so
// the mock dashboard shell stays ungated.
export default function AnaloguePage() {
  return (
    <SessionProvider>
      <Suspense fallback={<div className="p-4 text-xs text-muted-foreground">Loading…</div>}>
        <AnalogueWorkspace />
      </Suspense>
    </SessionProvider>
  );
}
