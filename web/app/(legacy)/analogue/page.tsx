import { Suspense } from "react";
import { AnalogueWorkspace } from "@/components/AnalogueWorkspace";

// Owner: Pavlo. Backend: services/analogue (Ingress path /api/analogue).
// UC-1..5 live under the "Пошук аналогів" tab (DrugSearch + AnaloguesList).
// Physician cart demo lives under "Призначення".
export default function AnaloguePage() {
  return (
    <main className="analogue-page">
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <AnalogueWorkspace />
      </Suspense>
    </main>
  );
}
