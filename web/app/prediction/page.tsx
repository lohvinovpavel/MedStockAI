import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Mykhailo. Backend: services/prediction (Ingress path /api/prediction).
export default function PredictionPage() {
  return (
    <main>
      <h1>prediction</h1>
      <ServiceHealth service="prediction" />
      <p>Build your page here. Call your endpoints with apiFetch("prediction", ...).</p>
    </main>
  );
}
