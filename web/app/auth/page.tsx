import { ServiceHealth } from "@/components/ServiceHealth";

// Owner: Tymur. Backend: services/auth (Ingress path /api/auth).
// TODO: replace this stub with the real login screen.
export default function AuthPage() {
  return (
    <main>
      <h1>auth</h1>
      <ServiceHealth service="auth" />
      <p>Build your page here. Call your endpoints with apiFetch("auth", ...).</p>
    </main>
  );
}
