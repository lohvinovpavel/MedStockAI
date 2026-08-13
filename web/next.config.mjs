/** @type {import('next').NextConfig} */
const analogueOrigin = process.env.ANALOGUE_PROXY_ORIGIN ?? "http://127.0.0.1:8002";
const inventoryOrigin = process.env.INVENTORY_PROXY_ORIGIN ?? "http://127.0.0.1:8001";

export default {
  // Required by the runtime stage of web/Dockerfile.
  output: "standalone",
  experimental: {
    // next dev rewrite proxy is 30s by default. UC-5 waits on RxNorm + Gemini
    // (20s/call, plus 429 backoff). Too short → ECONNRESET and a 500 in the UI
    // even though analogue would have returned the unfiltered Full list.
    proxyTimeout: 120_000,
  },
  async rewrites() {
    // Ingress does this in cluster. Local `next dev` has no ingress, so proxy
    // same-origin /api/analogue and /api/inventory to the FastAPI apps.
    if (process.env.NODE_ENV === "production") {
      return [];
    }
    return [
      { source: "/api/analogue/:path*", destination: `${analogueOrigin}/:path*` },
      { source: "/api/inventory/:path*", destination: `${inventoryOrigin}/:path*` },
    ];
  },
};
