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
    // Override with VITE_PORT in .env if this port is taken on your machine.
    // VITE_CLERK_SIGN_IN_URL / _SIGN_UP_URL must agree with it, or Clerk
    // redirects to a dead port.
    port: Number(process.env.VITE_PORT) || 5173,
    // Fail loudly instead of silently auto-incrementing, so the port in use
    // and the Clerk redirect URLs can never drift apart unnoticed.
    strictPort: true,
    proxy: {
      // Stream SSE through untouched. `changeOrigin` keeps the Host header
      // consistent; no compression is applied by Vite's proxy, so tokens
      // arrive incrementally. Verified by gate 6.
      "/api": {
        target: `http://localhost:${process.env.BFF_PORT || 8787}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
