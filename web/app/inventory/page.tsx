import { ServiceHealth } from "@/components/ServiceHealth";
import { StockLookup } from "@/components/StockLookup";

// Owner: Pavlo. Backend: services/inventory (Ingress path /api/inventory).
export default function InventoryPage() {
  return (
    <main>
      <h1>Inventory</h1>
      <p>
        Check whether a known preparation is on the hospital shelf. Paste an RxCUI, or
        follow Check inventory after selecting a drug.
      </p>
      <ServiceHealth service="inventory" />
      <StockLookup />
    </main>
  );
}
