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
      "/v1": "http://127.0.0.1:8000",
    },
    warmup: { clientFiles: ["./index.html", "./styles.css", "./src/main.js"] },
  },
});
