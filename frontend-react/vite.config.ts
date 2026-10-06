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
    // 5173 was taken by an unrelated project on this machine. Change the port
    // here AND the two VITE_CLERK_*_URL values in .env -- they must agree, or
    // Clerk redirects to a dead port.
    port: Number(process.env.VITE_PORT) || 5180,
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
