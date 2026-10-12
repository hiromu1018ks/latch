// O-07: E2E実行中にPOST /v1/sessions(セッション開始イベント・04 §2・05 §19)が
// 発火することをnetwork観測で証明する。あわせてDB側(sessions表)でも裏取りし、
// トークン未接続(未認証)の起動では発火しないことも確認する(計測の対象=認証済み
// ユーザーのDAU/再来訪のため)。
import { test, expect } from "./helpers.js";
import { cleanupAll, sql } from "./helpers.js";

test.afterAll(cleanupAll);

test("認証済み起動でPOST /v1/sessionsが発火しDBへ記録される(O-07)", async ({ page, user }) => {
  const fired = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().endsWith("/v1/sessions")) {
      fired.push(req);
    }
  });
  await page.goto("/");
  // ビーコンは起動effectでfire-and-forget発火。network観測で少なくとも1回
  await expect
    .poll(() => fired.length, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(1);
  // DB側証明: sessions表へuser_idと時刻のみの行(05 §19)
  await expect
    .poll(
      () =>
        Number(
          sql(`SELECT count(*) FROM sessions WHERE user_id='${user.userId}'::uuid;`),
        ),
      { timeout: 10_000 },
    )
    .toBeGreaterThanOrEqual(1);
});

test("未認証の起動ではPOST /v1/sessionsは発火しない(O-07・計測対象外)", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const fired = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().endsWith("/v1/sessions")) {
      fired.push(req);
    }
  });
  await page.goto("/");
  await page.waitForTimeout(1_500); // トークンパネルが表示される画面で待機
  expect(fired).toHaveLength(0);
  await expect(page.locator("#tokenPanel")).toBeVisible();
  await context.close();
});
