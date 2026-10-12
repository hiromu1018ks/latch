// P2残: G-09(parse 429)・G-10(保存429)・G-12(お知らせpopover+既読)・G-14(ブロック解除persist)。
// 429はpage.routeでの注入(実APIをレート限界まで叩く代わりにテスト側で応答を差し替え)。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture, api, sql, futureTimeText, provisionUser } from "./helpers.js";

test.afterAll(cleanupAll);

test("parse 429は専用文言で表示される(C3/G-09)", async ({ page }) => {
  await page.goto("/");
  await page.route("**/v1/intents/parse", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "RATE_LIMITED", message: "rate limited", details: null } }),
    }),
  );
  await page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  await expect(page.getByText("操作が集中しています。少し時間おいてもう一度お試しください。").or(page.getByText(/操作が集中/))).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("#readButton")).toBeDisabled();
});

test("保存429はerrorPlacementの文言を出し、再試行で通る(D7/G-10)", async ({ page }) => {
  await page.goto("/");
  await page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  await expect(page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("#readButton").click();
  await expect(page.locator("#confirmScreen")).toBeVisible();
  await page.getByRole("button", { name: "時間を編集" }).click();
  await page.locator("#edit-time").fill(futureTimeText(1));
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByRole("button", { name: "場所を編集" }).click();
  await page.locator("#edit-location").fill("天文館");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByText("含む", { exact: true }).click();
  await page.locator("#expiry").selectOption({ index: 2 });

  // 1回目の保存だけ429を返す → globalエラー表示
  let injected = true;
  await page.route("**/v1/intents", (route) => {
    if (injected && route.request().method() === "POST") {
      injected = false;
      return route.fulfill({
        status: 429,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "RATE_LIMITED", message: "rate limited", details: null } }),
      });
    }
    return route.continue();
  });
  await page.locator("#depositButton").click();
  await expect(page.getByText("操作が集中しています。少し時間おいてもう一度お試しください。").or(page.getByText(/操作が集中/))).toBeVisible({ timeout: 15_000 });
  await expect(page).toHaveURL(/#\/confirm/);
  // 再試行(同一操作)で成功する
  await page.locator("#depositButton").click();
  await expect(page).toHaveURL(/#\/standby\?deposited=1/, { timeout: 15_000 });
});

test("お知らせpopoverが開けて既読化がサーバへ永続する(J4/G-12)", async ({ page, user }) => {
  const other = await pairLatchFixture({});
  sql(
    `INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (gen_random_uuid(), '${user.userId}'::uuid, 'proposal', '{"latch_id": "${other.latchId}"}'::jsonb, now());`,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "お知らせ" }).click();
  await expect(page.getByText("LATCH候補があります。").first()).toBeVisible({ timeout: 15_000 });
  // popoverを開くと未読が既読化される(POST read)→サーバ側でread_atが付く
  await expect
    .poll(async () => {
      const list = await api("GET", "/v1/notifications?limit=20", { token: user.access });
      return (list.data.items ?? []).some((item) => item.read_at != null);
    }, { timeout: 15_000 })
    .toBeTruthy();
  // 永続化: リロードして開き直しても行が残る(既読状態で)
  await page.reload();
  await page.getByRole("button", { name: "お知らせ" }).click();
  await expect(page.getByText("LATCH候補があります。").first()).toBeVisible({ timeout: 15_000 });
});

test("ブロック解除はリロード後も維持される(I3 persist/G-14)", async ({ page, user }) => {
  const other = await pairLatchFixture({});
  const blocked = await api("POST", `/v1/users/${other.userA.userId}/block`, { token: user.access });
  expect(blocked.status).toBe(201);
  await page.goto("/#/settings");
  await expect(page.getByText("ブロックしているユーザーはいません")).toBeHidden({ timeout: 15_000 });
  await page.getByRole("button", { name: /さんのブロックを解除/ }).first().click();
  await expect(page.getByText(/さんのブロックを解除しますか?/)).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "解除する" }).click();
  await expect(page.getByText("ブロックしているユーザーはいません")).toBeVisible();
  await page.reload();
  await expect(page.getByText("ブロックしているユーザーはいません")).toBeVisible({ timeout: 15_000 });
});

// U-08(受け入れ#27 UI面): 18〜19歳ユーザーは飲酒を含むIntentを作成できない。
// API/LLM面はstaging S5 D-10・G1 alcoholで実施済みのため、UIの422表示のみ証明する。
// ciのstub Parserはalcohol_involved=false固定のため、parse応答だけをrouteで
// 書き換える(上の429テストと同手法・実API応答のalcoholをtrueへ。実Parserが
// 「飲み」テキストでtrueを返すことはG1 alcoholゲートで実証済み)。サーバの
// 年齢検証(422 UNDER_AGE)とUI表示は本物を通す。
test("18〜19歳ユーザーの飲酒Intent作成は422 UNDER_AGEの全体注記で拒否される(U-08)", async ({ openAuthed }) => {
  const young = await provisionUser({ tag: "u08", birthDate: "2008-06-15" }); // 18歳(2026-10時点)
  const mine = await openAuthed(young, "/#/");
  await mine.page.route("**/v1/intents/parse", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.structured_intent.alcohol_involved = true;
    await route.fulfill({ response, json: body });
  });
  await mine.page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  await expect(mine.page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await mine.page.locator("#readButton").click();
  await expect(mine.page.locator("#confirmScreen")).toBeVisible();
  const timeText = futureTimeText(1);
  await mine.page.getByRole("button", { name: "時間を編集" }).click();
  await mine.page.locator("#edit-time").fill(timeText);
  await mine.page.getByRole("button", { name: "保存" }).click();
  await mine.page.getByRole("button", { name: "場所を編集" }).click();
  await mine.page.locator("#edit-location").fill("天文館");
  await mine.page.getByRole("button", { name: "保存" }).click();
  await mine.page.getByText("含む", { exact: true }).click();
  await mine.page.locator("#expiry").selectOption({ index: 2 });
  await expect(mine.page.locator("#depositButton")).toBeEnabled();
  await mine.page.locator("#depositButton").click();
  // 422 UNDER_AGE → errorPlacementの全体note(02に留まる・03へは行かない)
  await expect(mine.page.getByText("飲酒を含むIntentは20歳以上の方のみ作成できます。")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page).not.toHaveURL(/#\/standby/);
  await mine.close();
});
