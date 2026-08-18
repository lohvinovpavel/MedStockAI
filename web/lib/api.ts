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

/**
 * One event from `POST /api/analogue/copilot/chat`'s SSE stream — the shape
 * `_sse()` in services/analogue/app/copilot.py writes
 * (`event: <kind>\ndata: <json>\n\n`), typed here rather than left as
 * `{event: string, data: unknown}` so a caller can narrow on `.event` and
 * get the right `.data` shape for free.
 */
export type PatientCandidate = { id: string; full_name: string; date_of_birth: string };

export type CopilotEvent =
  | { event: "delta"; data: { text: string } }
  | { event: "tool_start"; data: { name: string; args: Record<string, unknown> } }
  | { event: "tool_end"; data: { name: string; ok: boolean; error?: string } }
  | { event: "tool_card"; data: { name: string; card: Record<string, any> } }
  | { event: "degraded"; data: { reason: string } }
  // A name-based patient lookup (services/analogue/app/copilot.py) matched
  // more than one patient. Candidates carry PHI (name + DOB) precisely
  // because they never went through Gemini -- this event bypasses the model
  // entirely so the user picks straight from what the DB returned.
  | { event: "patient_disambiguation"; data: { tool: string; query: string; candidates: PatientCandidate[] } }
  | { event: "done"; data: { request_id: string } };

export type CopilotMessage = { role: "user" | "model"; text: string };

/**
 * Streams one copilot turn. A generator, not a promise of the final text —
 * the whole point of this endpoint is that the caller can render deltas and
 * tool activity as they arrive, not wait for the connection to close.
 *
 * Not built on `apiFetch`: that helper always awaits `res.json()`, which
 * would buffer the entire SSE stream before returning anything.
 */
async function* readSse(res: Response): AsyncGenerator<CopilotEvent> {
  if (!res.ok || !res.body) {
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("medstock:unauthorized"));
    }
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* keep status text */
    }
    throw new ApiError(message, res.status);
  }

  // SSE frames are separated by a blank line; a chunk boundary can land
  // mid-frame, so what's read has to be buffered and split on "\n\n" rather
  // than trusted to line up with one `read()` call each.
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      yield {
        event: eventLine.slice("event: ".length),
        data: JSON.parse(dataLine.slice("data: ".length)),
      } as CopilotEvent;
    }
  }
}

export async function* streamCopilotChat(
  messages: CopilotMessage[],
  signal?: AbortSignal,
): AsyncGenerator<CopilotEvent> {
  const res = await fetch(`${SERVICES.analogue}/copilot/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  yield* readSse(res);
}

export async function* streamCopilotMessage(
  body: { conversation_id: string; text: string; focus?: Record<string, unknown> | null },
  signal?: AbortSignal,
): AsyncGenerator<CopilotEvent> {
  const res = await fetch(`${SERVICES.copilot}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  yield* readSse(res);
}
