// G-06: 取り下げの永続化(E3 persist・OBS-05)。G-08: 条件を直す編集モード
// (D8 persist・D9・E2/E5導線)。D8のGETパスはunitで検出不能(FG-06)。
import { test, expect } from "./helpers.js";
import { cleanupAll, createIntent, api } from "./helpers.js";

test.afterAll(cleanupAll);

test("取り下げるとdialog確認を経て意思が消え、リロード後も戻らない(E3)", async ({ page, user }) => {
  const intentId = await createIntent(user);
  expect(intentId).toBeTruthy();
  await page.goto("/#/standby");
  await expect(page.getByRole("article").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "取り下げる", exact: true }).first().click();
  await expect(page.getByText("この条件を取り下げますか？")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "取り下げる" }).click();
  await expect(page.getByText("取り下げました", { exact: true })).toBeVisible();
  await expect(page.getByText("取り下げ済み")).toBeVisible();
  // 永続化: リロード(=active再取得)でも意思は戻らない
  await page.reload();
  await expect(page.getByRole("article")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByText("ほかに待機中の意思はありません。")).toBeVisible();
  const after = await api("GET", `/v1/intents/${intentId}`, { token: user.access });
  // 取り下げ(cancelled)後のGETは本人にも404=契約挙動(物理削除なし・一覧からのみ消える)
  expect(after.status).toBe(404);
});

test("dialogのキャンセルでは取り下げない(E4)", async ({ page, user }) => {
  await createIntent(user);
  await page.goto("/#/standby");
  await expect(page.getByRole("article").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "取り下げる", exact: true }).first().click();
  await expect(page.getByText("この条件を取り下げますか？")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "やめておく" }).click();
  await expect(page.getByRole("article").first()).toBeVisible();
});

test("条件を直す→復元→書き換え→保存でPATCHされ、リロード後も変更が残る(D8/E2)", async ({ page, user }) => {
  const intentId = await createIntent(user);
  await page.goto("/#/standby");
  await expect(page.getByRole("article").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("link", { name: "条件を直す" }).click();
  await expect(page).toHaveURL(new RegExp(`#\\/confirm\\?intent=${intentId}`));
  await expect(page.locator("#confirmScreen")).toBeVisible();
  // GET復元: 人数行を3人へ書き換えて保存(PATCH)
  await page.getByRole("button", { name: "人数を編集" }).click();
  await page.locator("#edit-participants").fill("3人");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByText("含む", { exact: true }).click();
  await page.locator("#expiry").selectOption({ index: 2 });
  await page.locator("#depositButton").click();
  await expect(page).toHaveURL(/#\/standby/, { timeout: 15_000 });
  // 永続化: 03に残る+APIで書き換えを確認
  await page.reload();
  await expect(page.getByRole("article").first()).toBeVisible({ timeout: 15_000 });
  const after = await api("GET", `/v1/intents/${intentId}`, { token: user.access });
  expect(after.data.intent.structured_intent.participants).toMatchObject({ max: 3 });
});
