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
function shouldEndSession(service: ServiceName, path: string) {
  // /login 401 is "wrong password", not "session died". /healthz and friends
  // are public probes — a down analogue pod must not look like a logout.
  if (service !== "auth") return false;
  return path !== "/login" && path !== "/healthz" && path !== "/readyz" && path !== "/version";
}

/**
 * Carries the HTTP status alongside the message.
 *
 * Callers that must tell failures apart cannot do it from the message: 403
 * "forbidden" and 503 "unreachable" are different things to say to a user, and
 * a page that collapses them shows an empty list for both. Existing callers
 * that only read `.message` are unaffected — this is still an Error.
 */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch(service: ServiceName, path: string, init?: RequestInit) {
  const res = await fetch(`${SERVICES[service]}${path}`, {
    ...init,
    credentials: "include",
    headers: { "content-type": "application/json", ...init?.headers },
  });

  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && shouldEndSession(service, path)) {
      // Only auth 401s mean the cookie is gone. Analogue/patients health
      // checks and JWT-verify mismatches used to fire this too, and the
      // Analogues tab would bounce a still-valid physician session to /auth.
      window.dispatchEvent(new Event("medstock:unauthorized"));
    }
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        message = body.detail;
      }
    } catch {
      /* keep status text */
    }
    throw new ApiError(message, res.status);
  }
  return res.status === 204 ? null : res.json();
}
