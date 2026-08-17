import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Andrii. Backend: services/compliance (Ingress path /api/compliance).
// Director surface — compliance export (docs/services.md §2).
export default function CompliancePage() {
  return (
    <main>
      <h1>compliance</h1>
      <ServiceHealth service="compliance" />
      <p>Build your page here. Call your endpoints with {'apiFetch("compliance", ...)'}.</p>
    </main>
  );
}
