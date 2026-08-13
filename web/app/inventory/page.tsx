import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Pavlo. Backend: services/inventory (Ingress path /api/inventory).
export default function InventoryPage() {
  return (
    <main>
      <h1>inventory</h1>
      <ServiceHealth service="inventory" />
      <p>Build your page here. Call your endpoints with apiFetch("inventory", ...).</p>
    </main>
  );
}
