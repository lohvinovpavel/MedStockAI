import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Mykhailo. Backend: services/warehouse (Ingress path /api/warehouse).
export default function WarehousePage() {
  return (
    <main>
      <h1>warehouse</h1>
      <ServiceHealth service="warehouse" />
      <p>Build your page here. Call your endpoints with apiFetch("warehouse", ...).</p>
    </main>
  );
}
