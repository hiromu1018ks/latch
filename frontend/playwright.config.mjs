// UI E2E(Playwright)。実行前提: 対象環境が稼働中。
//  - ci      : compose常設API(make up・127.0.0.1:8000)+vite dev 5173(既定・変更なし)
//  - staging : make staging-up(nginx 18000)+vite dev 5174(proxy向き先を18000へ)
//  - cf      : make cf-dev相当のローカルminiflare(8787・build済みassets+D1)
// 実行するprojectはLATCH_E2E_PROJECTS(カンマ区切り・既定=ciのみ)で選ぶ。
// `npm run test:e2e`(=make e2e)はciのみ・従来挙動を維持する。
// 動画は全テストを常時録画し `npm run test:e2e:report` で人間閲覧用ギャラリー
// (e2e-recordings/<run>/index.html)を生成する。LATCH_E2E_SLOWMO=300 等で
// ゆっくり再生の録画も可能(視聴補助・既定は通常速度)。
import { defineConfig } from "@playwright/test";

// 空文字・未指定はci(従来どおり。shell profile由来の空LATCH_E2E_PROJECTSにも対応)
const rawProjects = (process.env.LATCH_E2E_PROJECTS ?? "").trim();
const wanted = new Set((rawProjects || "ci").split(",").map((s) => s.trim()).filter(Boolean));

// 選択名(ci/staging/cloudflare)→project名(ciは従来どおりchromium)
const gateToProject = { ci: "chromium", staging: "staging", cloudflare: "cloudflare" };
const projectNames = new Set(
  [...wanted].map((w) => gateToProject[w] ?? w),
);

const projects = [
  {
    name: "chromium",
    testDir: "./tests/e2e",
    use: { browserName: "chromium" },
  },
  {
    name: "staging",
    testDir: "./tests/e2e-staging",
    use: {
      browserName: "chromium",
      baseURL: "http://127.0.0.1:5174",
    },
  },
  {
    name: "cloudflare",
    testDir: "./tests/e2e-cloudflare",
    use: {
      browserName: "chromium",
      baseURL: "http://127.0.0.1:8787",
    },
  },
].filter((p) => projectNames.has(p.name));

// 実行対象projectのwebServerのみ立てる(ci=5173・staging=5174proxy18000・
// cf=scripts/cf-e2e-server.mjsがbuild+D1マイグレーション+pywrangler devを担ぐ)
const webServers = [];
if (wanted.has("ci")) {
  webServers.push({
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  });
}
if (wanted.has("staging")) {
  webServers.push({
    command: "LATCH_VITE_PROXY_TARGET=http://127.0.0.1:18000 npm run dev -- --port 5174 --strictPort",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: true,
    timeout: 60_000,
  });
}
if (wanted.has("cloudflare")) {
  webServers.push({
    command: "node scripts/cf-e2e-server.mjs",
    url: "http://127.0.0.1:8787/health",
    reuseExistingServer: true,
    timeout: 240_000,
  });
}

export default defineConfig({
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5173", // 既定=ci(chromium project)。staging/cfはproject側で上書き
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
  projects,
  // webServer(単数キー)に配列を渡す形式(実行対象projectの分のみ登録)
  webServer: webServers,
});
