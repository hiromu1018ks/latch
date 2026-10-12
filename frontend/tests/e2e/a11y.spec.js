// Q-03: @axe-core/playwrightによる全画面a11y自動検査(違反ゼロpin)。
// 走査対象=UI v2の全ルート(01/02入口/03/04/05/一覧2種/設定/同意/お知らせpopover)。
// 違反が出た場合は修正または判断の文書化(matrix §12)までをこのspecが強制する。
// ci project専用(fixtureがSQL直挿入のため。staging/cfは対象外)。
import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./helpers.js";
import { cleanupAll, createIntent, insertPairLatch, pairLatchFixture, provisionUser } from "./helpers.js";

test.afterAll(cleanupAll);

async function expectNoViolations(page, label) {
  // 入場アニメーション(rise等)のopacity中間値で誤検出しないよう・走査は
  // prefers-reduced-motion(全アニメ停止)で行う。本番のreduce設定は
  // styles.css冒頭のmedia queryが担い、ここでは採取条件として使う
  await page.emulateMedia({ reducedMotion: "reduce" });
  const results = await new AxeBuilder({ page })
    // WCAG 2.1 AA相当のbest-practiceルール。実験的ルールは含めない(文書化判断)
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const violations = results.violations;
  // 違反の内訳を出力(失敗時の調査用)
  if (violations.length > 0) {
    console.error(
      `[a11y:${label}]`,
      JSON.stringify(
        violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          help: v.help,
          nodes: v.nodes.slice(0, 8).map((n) => ({
            target: n.target,
            html: n.html.slice(0, 120),
            any: n.any.map((a) => a.data).slice(0, 1),
          })),
        })),
        null,
        2,
      ),
    );
  }
  expect(violations, `a11y violations on ${label}`).toEqual([]);
}

test("01 ホーム(未認証=トークンパネル)", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/");
  await page.locator("#tokenPanel").waitFor({ state: "visible", timeout: 15_000 });
  await expectNoViolations(page, "home-unauthenticated");
  await context.close();
});

test("01 ホーム(認証済み)", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#readButton")).toBeVisible({ timeout: 15_000 });
  await expectNoViolations(page, "home");
});

test("02 読み解き確認(parse往復後)", async ({ page }) => {
  await page.goto("/");
  await page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  await expect(page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("#readButton").click();
  await expect(page.locator("#confirmScreen")).toBeVisible();
  await expectNoViolations(page, "confirm");
});

test("03 待機中(Active Intentあり)", async ({ page, user }) => {
  await createIntent(user);
  await page.goto("/#/standby");
  await expect(page.getByText("条件が揃うのを、待っています。")).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(page, "standby");
});

test("04 候補(全形)と05 成立・チャット", async ({ openAuthed }) => {
  const pair = await pairLatchFixture({});
  // 04(全形・回答open状態)。latchの参加者(userA)で開く(所有者外は404/403)
  const prop = await openAuthed(pair.userA, `/#/latches/${pair.latchId}`);
  await expect(prop.page.getByText("条件が、揃いそうです。")).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(prop.page, "proposal");
  await prop.close();
  // 05(matched fixture・チャット)。同一Intent対へのopen二重挿入は
  // uq_latches_intent_ids_openで禁止のため別ペアで作る(V1-P2知見)
  const matchedPair = await pairLatchFixture({ status: "matched" });
  const matched = await openAuthed(matchedPair.userA, `/#/latches/${matchedPair.latchId}`);
  await expect(matched.page.getByText("LATCHしました。")).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(matched.page, "matched");
  await matched.close();
});

test("簡易一覧2種+設定+同意", async ({ page, user }) => {
  await page.goto("/#/proposals");
  await expect(page.locator("#listsScreen, .page-main").first()).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(page, "proposals-list");
  await page.goto("/#/matched");
  await expectNoViolations(page, "matched-list");
  await page.goto("/#/settings");
  await expect(page.getByText("ブロックしているユーザーはいません")).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(page, "settings");
  await page.goto("/#/onboarding");
  await expect(page.getByText("LLM送信の同意", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(page, "onboarding");
});

test("お知らせpopover(開状態)", async ({ page, user }) => {
  const other = await provisionUser({});
  const intentA = await createIntent(other);
  const intentB = await createIntent(user);
  const latchId = insertPairLatch(intentA, intentB, { status: "proposed" });
  const { sql } = await import("./helpers.js");
  sql(
    `INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (gen_random_uuid(), '${user.userId}'::uuid, 'proposal', '{"latch_id": "${latchId}"}'::jsonb, now());`,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "お知らせ" }).click();
  await expect(page.getByText("LATCH候補があります。").first()).toBeVisible({
    timeout: 15_000,
  });
  await expectNoViolations(page, "notice-popover");
});
