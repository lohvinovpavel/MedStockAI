import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Pavlo. Backend: services/analogue (Ingress path /api/analogue).
// Pharmacist surface — review queue (docs/services.md §2).
export default function AnaloguePage() {
  return (
    <main>
      <h1>analogue</h1>
      <ServiceHealth service="analogue" />
      <p>Build your page here. Call your endpoints with apiFetch("analogue", ...).</p>
    </main>
  );
}
