import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist/client",
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: {
      // E2EからはLATCH_VITE_PROXY_TARGETでstaging(18000)等へ切替(e2e-staging project)。
      // 未指定の既定はci常設API(8000) — 従来挙動と同一
      "/v1": process.env.LATCH_VITE_PROXY_TARGET ?? "http://127.0.0.1:8000",
    },
    warmup: { clientFiles: ["./index.html", "./styles.css", "./src/main.js"] },
  },
});
