// UI E2E(Playwright)。実行前提: compose常設APIが稼働中(make up・127.0.0.1:8000)。
// vite dev が /v1 を 8000 へproxyするため、フロントとAPIでOriginを揃える必要はない。
// 動画は全テストを常時録画し `npm run test:e2e:report` で人間閲覧用ギャラリー
// (e2e-recordings/<run>/index.html)を生成する。LATCH_E2E_SLOWMO=300 等で
// ゆっくり再生の録画も可能(視聴補助・既定は通常速度)。
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    video: "on",
    trace: "retain-on-failure",
    launchOptions: process.env.LATCH_E2E_SLOWMO
      ? { slowMo: Number(process.env.LATCH_E2E_SLOWMO) }
      : undefined,
  },
  reporter: [
    ["list"],
    ["html", { open: "never" }],
    ["json", { outputFile: "e2e-report/results.json" }],
  ],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
