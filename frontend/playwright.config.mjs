// UI E2E(Playwright)。実行前提: compose常設APIが稼働中(make up・127.0.0.1:8000)。
// vite dev が /v1 を 8000 へproxyするため、フロントとAPIでOriginを揃える必要はない。
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
