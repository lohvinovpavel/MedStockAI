import { ServiceHealth } from "@/components/ServiceHealth";
import { DrugSearch } from "@/components/DrugSearch";

// Owner: Pavlo. Backend: services/analogue (Ingress path /api/analogue).
// UC-1: resolve a typed name to a DrugIdentity (RxCUI SCD/SBD).
export default function AnaloguePage() {
  return (
    <main>
      <h1>Find a drug</h1>
      <p>
        Search by name, confirm a preparation to see its shelf status, then find
        analogues ranked in-stock first with High / Normal / Low / Out of stock.
      </p>
      <ServiceHealth service="analogue" />
      <DrugSearch />
    </main>
  );
}
