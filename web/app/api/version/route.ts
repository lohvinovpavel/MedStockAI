import pkg from "../../../package.json";

// Server-side only: GIT_SHA is a plain (non-NEXT_PUBLIC) env var baked into
// the image at build time (web/Dockerfile) — this route is how the browser
// gets it, mirroring each backend's own GET /version. semver comes straight
// from package.json, same as each backend reads its own pyproject.toml.
export async function GET() {
  return Response.json({
    service: "web",
    version: process.env.GIT_SHA ?? "unknown",
    semver: pkg.version,
  });
}
