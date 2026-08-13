// Ingress path segment per backend service — the one place this mapping is
// written down. Mirrors deploy/k8s/ingress.yaml and docs/services.md §3.
// Add a service here, not in each page.
export const SERVICES = {
  auth: "/api/auth",
  inventory: "/api/inventory",
  analogue: "/api/analogue",
  compliance: "/api/compliance",
  patients: "/api/patients", // patient-profiling
  prediction: "/api/prediction",
  warehouse: "/api/warehouse",
} as const;

export type ServiceName = keyof typeof SERVICES;
