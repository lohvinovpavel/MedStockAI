/** @type {import('next').NextConfig} */
const authOrigin = process.env.AUTH_PROXY_ORIGIN ?? "http://127.0.0.1:8000";
const inventoryOrigin = process.env.INVENTORY_PROXY_ORIGIN ?? "http://127.0.0.1:8001";
const analogueOrigin = process.env.ANALOGUE_PROXY_ORIGIN ?? "http://127.0.0.1:8002";
const patientsOrigin = process.env.PATIENTS_PROXY_ORIGIN ?? "http://127.0.0.1:8003";

export default {
  // Required by the runtime stage of web/Dockerfile.
  output: "standalone",

  experimental: {
    // next dev rewrite proxy is 30s by default. UC-5 waits on RxNorm + Gemini
    // (20s/call, plus 429 backoff). Too short → ECONNRESET and a 500 in the UI
    // even though analogue would have returned the unfiltered Full list.
    proxyTimeout: 120_000,
  },

  // Dev only. In a cluster the Ingress owns /api/* and never reaches Next
  // (deploy/k8s/ingress.yaml), so this list is empty in production. One port
  // per service, so two people can run two backends locally at once — add a
  // line here (and an env var above) as each remaining service grows real
  // endpoints.
  async rewrites() {
    if (process.env.NODE_ENV === "production") return [];
    return [
      { source: "/api/auth/:path*", destination: `${authOrigin}/:path*` },
      { source: "/api/inventory/:path*", destination: `${inventoryOrigin}/:path*` },
      { source: "/api/analogue/:path*", destination: `${analogueOrigin}/:path*` },
      { source: "/api/patients/:path*", destination: `${patientsOrigin}/:path*` },
    ];
  },
};
