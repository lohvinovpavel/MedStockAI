import { SERVICES, ServiceName } from "./services";

/**
 * Every backend call goes through this. Same-origin by design — no base
 * URL, Ingress routes /api/<service> to the right pod (docs/services.md §2).
 * Use it from every service page below so there is exactly one place that
 * knows how a request is authenticated.
 *
 * Authentication is the medstock_token cookie: httpOnly, so no code here
 * can read it and none needs to — the browser attaches it. That is the
 * point (docs/auth-spec.md §4). Nothing in web/ ever holds a token.
 */
export async function apiFetch(service: ServiceName, path: string, init?: RequestInit) {
  const res = await fetch(`${SERVICES[service]}${path}`, {
    ...init,
    credentials: "include",
    headers: { "content-type": "application/json", ...init?.headers },
  });

  if (!res.ok) {
    throw new Error(`${service}${path} -> ${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? null : res.json();
}
