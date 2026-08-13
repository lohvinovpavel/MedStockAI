import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Andrii. Backend: services/patient-profiling (Ingress path /api/patients).
export default function PatientsPage() {
  return (
    <main>
      <h1>patients</h1>
      <ServiceHealth service="patients" />
      <p>Build your page here. Call your endpoints with apiFetch("patients", ...).</p>
    </main>
  );
}
