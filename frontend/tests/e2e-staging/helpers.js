// staging(nginx 18000)向けE2Eの共通helper。本体の設定切替は ../e2e/helpers.js が
// LATCH_E2E_TARGET=staging(npm scriptが設定)で担うため、ここではstaging特有の
// 実パイプライン待ち・UI預け入れ操作・グラウンドトゥルースfixtureを提供する。
//
// 前提(npm run test:e2e:staging):
//   make staging-up + staging-migrate + staging-geo-import 済み・Clock=実時間
//   (ControlledClockのキー不在=実時間。ずれていれば最初に戻す)
import { execFileSync } from "node:child_process";
import { api, provisionUser, createIntent, test, expect } from "../e2e/helpers.js";

export { api, provisionUser, createIntent, cleanupAll, test, expect } from "../e2e/helpers.js";
export const STAGING_REDIS = "latch-staging-redis-1";

// stagingの仮想Clockが実時間から大きくずれていると意図作成の時刻設計が崩れる
// (過去のM4ハーネスがClockをD+6まで進めたまま残るケース)。5分以上ずれていれば
// 制御キーを消して実時間へ戻す(キー不在=オフセット0=実時間がClock契約の正規状態)。
export function ensureStagingClockReal() {
  const raw = execFileSync(
    "docker",
    ["exec", STAGING_REDIS, "redis-cli", "GET", "latch:clock:control"],
    { encoding: "utf8" },
  ).trim();
  if (!raw) return; // 未設定=実時間
  const { real, virtual } = JSON.parse(raw);
  if (Math.abs(virtual - real) > 300) {
    execFileSync("docker", ["exec", STAGING_REDIS, "redis-cli", "DEL", "latch:clock:control"]);
  }
}

// 実パイプラインの提案昇格待ち(debounce 10s+tick≤60s・実測15秒前後・
// stagingハーネスwait_for_latchと同じく余裕をもって180s)。
export async function waitForLatch(user, { status = "proposed", timeoutMs = 180_000 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const r = await api("GET", "/v1/latches", { token: user.access });
    const hit = (r.data?.items ?? []).filter((i) => i.status === status);
    if (hit.length > 0) return hit[0];
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  throw new Error(`waitForLatch timeout: ${status} が現れない (user=${user.subject})`);
}

export async function expectNoLatch(user, { graceMs = 0 } = {}) {
  if (graceMs) await new Promise((resolve) => setTimeout(resolve, graceMs));
  const r = await api("GET", "/v1/latches", { token: user.access });
  return (r.data?.items ?? []).length;
}

// 01入力→parse→02行編集→預ける→03着地(P-01のUI作成経路。stub Parserは固定応答
// ・時間行と場所行を実ユーザーが書き直す運用操作)。timeTextは両ユーザーで同じ値を
// 呼び出し側で1回だけ計算して渡す(時刻一致がLayer 1の前提)。
export async function depositViaUi(page, { text, timeText }) {
  await page.goto("/#/");
  await page.locator("#intentText").fill(text);
  await expect(page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("#readButton").click();
  await expect(page.locator("#confirmScreen")).toBeVisible();
  await page.getByRole("button", { name: "時間を編集" }).click();
  await page.locator("#edit-time").fill(timeText);
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByRole("button", { name: "場所を編集" }).click();
  await page.locator("#edit-location").fill("天文館");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByText("含む", { exact: true }).click();
  await page.locator("#expiry").selectOption({ index: 2 });
  await expect(page.locator("#depositButton")).toBeEnabled();
  await page.locator("#depositButton").click();
  await expect(page).toHaveURL(/#\/standby\?deposited=1/, { timeout: 15_000 });
  await expect(page.getByText("預けました。")).toBeVisible();
}

// グラウンドトゥルースIntent(A-01)。stagingハーネス(helpers_scenario)の形状を
// 基準に・境界値(時刻・距離・人数・予算・visibility)だけ差し替えられる形。
export async function createGroundTruthIntent(user, overrides = {}) {
  const {
    when,
    peerWhen = when, // 時間不隣接ケースで片側だけずらす
    category = "activity",
    secondary = "散策",
    visibility = "summary_only",
    participants = [2, 2],
    location = "天文館",
    radiusM = 1000,
    budgetMax = 3000,
    expiresAt,
  } = overrides;
  const structured = {
    category: { primary: category, secondary },
    alcohol_involved: false,
    time: { start: peerWhen },
    location: { name: location, radius_m: radiusM },
    budget: budgetMax == null ? null : { max: budgetMax },
    participants: { min: participants[0], max: participants[1] },
    soft_constraints: [],
    visibility,
    notification_level: "proposals_only",
  };
  if (expiresAt) structured.expires_at = expiresAt;
  const res = await api("POST", "/v1/intents", {
    token: user.access,
    body: {
      raw_text: `gt ${location} ${peerWhen}`,
      status: "active",
      structured_intent: structured,
    },
  });
  if (res.status !== 201) {
    throw new Error(`ground truth intent failed: ${res.status} ${JSON.stringify(res.data)}`);
  }
  return res.data.intent.id;
}

// A-01検証ユーザーの後片付け(退会APIでIntent/latchごとカスケード)。
// afterAllでなくfinallyで呼ぶ=リトライ前に同一スロットの残留を消す。
export async function deleteUsers(users) {
  for (const u of users) {
    try {
      await api("DELETE", "/v1/users/me", { token: u.access });
    } catch {
      /* best-effort */
    }
  }
}
