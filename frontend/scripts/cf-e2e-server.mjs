// E-01(backlog §1): Cloudflare実装へのUI E2E接続用webServer。
// やること: ①frontend build(Workers Assets配信のため) ②make cf-sync(本体latchの
// rsync) ③専用persist領域(.wrangler-e2e)へD1マイグレーション+地物seedを適用
// (未実施の場合のみ) ④pywrangler dev(8787)をforegroundで起動。
// 既に8787が応答する場合はPlaywright側のreuseExistingServerが起動を丸ごと省略する。
// D1のstateはcloudflare/api/.wrangler-e2eに閉じる(手動cf-devの.wranglerとは分離。
// --persist-toでexecuteとdevが同一sqliteを参照する — 過去の「--localが別sqliteを
// 参照する」問題の回避)。
import { execSync, spawn } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const REPO = path.resolve(FRONTEND, "..");
const CF_API = path.join(REPO, "cloudflare/api");
// persist先はcloudflare/api配下に置かない(wrangler devのファイル監視がD1書き込みを
// 検知してリロードループするため・リポジトリ直下のgit管理外ディレクトリへ)
const PERSIST = path.join(REPO, ".cf-e2e-d1");

const CF_ENV = {
  ...process.env,
  UV_CACHE_DIR: path.join(REPO, ".uv-cache"),
  UV_PYTHON_INSTALL_DIR: path.join(REPO, ".uv-python"),
  npm_config_cache: path.join(REPO, ".npm-cache"),
  XDG_CONFIG_HOME: path.join(REPO, ".xdg-config"),
};

const run = (cmd, cwd) => execSync(cmd, { cwd, env: CF_ENV, stdio: "inherit" });

// ①フロントbuild(assets配信)
run("npm run build", FRONTEND);
// ②本体実装のコピー(bundlerはsymlinkを辿らない・make cf-syncと同コマンド)
run("rsync -a --delete --exclude __pycache__ --exclude .venv* backend/src/latch/ cloudflare/api/latch/", REPO);

const d1 = (args) =>
  execSync(`uv run pywrangler d1 execute latch-core ${args}`, {
    cwd: CF_API,
    env: CF_ENV,
    encoding: "utf8",
  });

// ③D1マイグレーション+地物seed(users表がなければ初回と判定)
let initialized = false;
try {
  const out = d1(
    `--local --persist-to ${PERSIST} --command "SELECT count(*) FROM users"`,
  );
  initialized = !/"error"/.test(out) && /"success": true/.test(out);
} catch {
  initialized = false;
}
if (!initialized) {
  const migrations = path.join(REPO, "cloudflare/migrations");
  for (const f of readdirSync(migrations).filter((n) => n.endsWith(".sql")).sort()) {
    console.log(`[cf-e2e-server] D1 migration: ${f}`);
    d1(`--local --persist-to ${PERSIST} --file ${path.join(migrations, f)}`);
  }
  d1(
    `--local --persist-to ${PERSIST} --file ${path.join(REPO, "backend/tests/cloudflare/fixtures/geofeatures_seed.sql")}`,
  );
  console.log("[cf-e2e-server] D1 initialized (migrations + geofeatures seed)");
}

// ④cf-dev(手動make cf-devと同じCF_ENV・専用persist・foreground)
console.log("[cf-e2e-server] starting pywrangler dev on 8787");
const child = spawn(
  "uv",
  ["run", "pywrangler", "dev", "--port", "8787", "--persist-to", PERSIST],
  { cwd: CF_API, env: CF_ENV, stdio: "inherit" },
);
child.on("exit", (code) => process.exit(code ?? 0));
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    child.kill(sig);
  });
}
