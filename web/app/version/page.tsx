"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { SERVICES, ServiceName } from "@/lib/services";

type Row = { service: string; version: string; semver?: string };

// One place to see what's actually deployed on this cluster right now —
// each backend's GIT_SHA (baked in at image build) plus web's own, read
// live instead of trusting whatever the last person remembers deploying.
export default function VersionPage() {
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    const services = Object.keys(SERVICES) as ServiceName[];
    Promise.allSettled([
      fetch("/api/version").then((r) => r.json()),
      ...services.map((service) => apiFetch(service, "/version")),
    ]).then((results) => {
      setRows(
        results.map((result, i) =>
          result.status === "fulfilled"
            ? result.value
            : { service: i === 0 ? "web" : services[i - 1], version: `unreachable (${result.reason})` }
        )
      );
    });
  }, []);

  return (
    <main>
      <h1>Deployed versions</h1>
      <table>
        <thead>
          <tr>
            <th>service</th>
            <th>semver</th>
            <th>commit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.service}>
              <td>{row.service}</td>
              <td>
                <code>{row.semver ?? "—"}</code>
              </td>
              <td>
                <code>{row.version}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
