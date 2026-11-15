import type { NextConfig } from "next";

// Authenticated (NovaForge) deployments: the console reaches the backend through
// this same-origin proxy so the session cookie belongs to the console's origin.
// Build the console with NEXT_PUBLIC_API_URL=/api/pp. The destination is read at
// build time from PASHUPAT_BACKEND_URL (default: the local backend). NovaForge's
// registered redirect URI is <console origin>/api/pp/auth/callback.
const backend = (process.env.PASHUPAT_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/pp/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
