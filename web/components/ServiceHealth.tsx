"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { ServiceName } from "@/lib/services";

/**
 * Proves the wiring for one service — browser to Ingress to pod — before
 * any real UI exists. Every service page renders this, then builds its own
 * content below it. Keep this component itself boring; it is the one thing
 * every page shares, so changes here touch all seven.
 */
export function ServiceHealth({ service }: { service: ServiceName }) {
  const [status, setStatus] = useState("checking…");

  useEffect(() => {
    apiFetch(service, "/healthz")
      .then((body) => setStatus(JSON.stringify(body)))
      .catch((err) => setStatus(`unreachable (${err.message})`));
  }, [service]);

  return (
    <p className="font-mono text-[11px] text-muted-foreground">
      backend health: <span className="text-foreground">{status}</span>
    </p>
  );
}
