import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Fail loudly instead of silently auto-incrementing. The Clerk redirect
    // URLs in .env.example (VITE_CLERK_SIGN_IN_URL / _SIGN_UP_URL) assume
    // http://localhost:5173, so a drift to 5174 silently breaks sign-in and
    // sign-up. A hard "port already in use" error is the correct failure.
    strictPort: true,
    proxy: {
      // Stream SSE through untouched. `changeOrigin` keeps the Host header
      // consistent; no compression is applied by Vite's proxy, so tokens
      // arrive incrementally. Verified by gate 6.
      "/api": {
        target: "http://localhost:8787",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
