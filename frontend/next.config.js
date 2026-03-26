/** @type {import('next').NextConfig} */

// TAURI_BUILD=1 is set by the "build:tauri" npm script.
// In that mode we emit a fully-static export consumed by Tauri's WebView.
// In regular dev / web-server mode we keep the /api proxy to the FastAPI backend.
const isTauriBuild = process.env.TAURI_BUILD === "1";

const nextConfig = {
  ...(isTauriBuild
    ? {
        output: "export",
        // Static export does not support image optimisation — disable it.
        images: { unoptimized: true },
      }
    : {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: "http://localhost:8000/:path*",
            },
          ];
        },
      }),
};

module.exports = nextConfig;
