import { SERVICES, ServiceName } from "./services";

/**
 * Every backend call goes through this. Same-origin by design — no base
 * URL, Ingress routes /api/<service> to the right pod (docs/services.md §2).
 * Use it from every service page below so there is exactly one place that
 * knows how a request is authenticated.
 *
 * ponytail: the auth header is a placeholder. There is no /login yet
 * (services/auth is a healthz-only stub — see services/auth/README.md), so
 * this reads a token from localStorage under a fixed key. Once real login
 * exists, change how `token` is obtained here — every page already calls
 * through apiFetch, so nothing else needs to change.
 */
export async function apiFetch(service: ServiceName, path: string, init?: RequestInit) {
  const token = typeof window !== "undefined" ? window.localStorage.getItem("medstock_token") : null;

  const res = await fetch(`${SERVICES[service]}${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        message = body.detail;
      }
    } catch {
      /* keep status text */
    }
    throw new Error(message);
  }
  return res.json();
}
