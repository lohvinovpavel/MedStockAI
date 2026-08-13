/** @type {import('next').NextConfig} */
export default {
  // Required by the runtime stage of web/Dockerfile.
  output: "standalone",

  // Dev only. In a cluster the Ingress owns /api/* and never reaches Next
  // (deploy/k8s/ingress.yaml), so this list is empty in production.
  // ponytail: every service maps to one local port, so only one backend can
  // run at a time locally. Split it per service when two people need two
  // backends up at once.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:service/:path*", destination: "http://localhost:8000/:path*" }];
  },
};
