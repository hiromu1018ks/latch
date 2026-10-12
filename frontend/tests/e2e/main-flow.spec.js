// G-02: 主幹作成フロー(C1/C2/C4/C5/D5/E1)。composer配線(型入力→parse→chips→
// CTA)はunit全体で未証明(OBS-03)のため、本E2Eが最初の証明。
// stub Parserは固定応答(東京駅・過去時刻)なので、02で行編集により場所=天文館
// (ci DBの既存地物)・時刻=近未来へ直してから保存する。
import { test, expect } from "./helpers.js";
import { cleanupAll, futureTimeText } from "./helpers.js";

test.afterAll(cleanupAll);

test("空テキストでは読み解くCTAが無効(C2)", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#readButton")).toBeDisabled();
});

test("301字を貼ってもtextareaは300字に丸められる(C2)", async ({ page }) => {
  await page.goto("/");
  await page.locator("#intentText").fill("あ".repeat(301));
  const len = await page.locator("#intentText").evaluate((el) => el.value.length);
  expect(len).toBe(300);
});

test("例文chipをクリックするとcomposerに挿入される(C4)", async ({ page }) => {
  await page.goto("/");
  await page.locator("#exampleChips button").first().click();
  const value = await page.locator("#intentText").evaluate((el) => el.value);
  expect(value.length).toBeGreaterThan(0);
});

test("入力→読み取りchips→読み解く→行編集→預ける→03着地→リロードでも残る(C1/D1/D2/D5/E1)", async ({ page }) => {
  await page.goto("/");
  await page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  // 1秒debounce+parseでchipsが生え、CTAが有効化する
  await expect(page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("#readButton")).toBeEnabled();
  await page.locator("#readButton").click();
  await expect(page.locator("#confirmScreen")).toBeVisible();
  await expect(page.locator("#confirmRows .slot-row")).toHaveCount(5);

  // 時間行を近未来へ書き換え(stub既定は過去時刻のため保存422になる)
  const timeText = futureTimeText(1);
  await page.getByRole("button", { name: "時間を編集" }).click();
  await page.locator("#edit-time").fill(timeText);
  await page.getByRole("button", { name: "保存" }).click();
  // 場所行を天文館へ書き換え(stub既定の東京駅はci地物の範囲外)
  await page.getByRole("button", { name: "場所を編集" }).click();
  await page.locator("#edit-location").fill("天文館");
  await page.getByRole("button", { name: "保存" }).click();

  // 人数確認seg(D-04・radio)でCTA有効化・期限は常に「明日 5:00」(startより後になる選択肢)
  await page.getByText("含む", { exact: true }).click();
  await page.locator("#expiry").selectOption({ index: 2 });
  await expect(page.locator("#depositButton")).toBeEnabled();
  await page.locator("#depositButton").click();

  await expect(page).toHaveURL(/#\/standby\?deposited=1/);
  await expect(page.getByText("預けました。")).toBeVisible();
  await expect(page.getByText("あとは、待つだけ。")).toBeVisible();

  // 永続化: リロード後も意思が03に残る(D5 persist・FG相当の実再取得証明)
  await page.reload();
  await expect(page.getByRole("article").first()).toBeVisible();
  await expect(page.locator(".sb-tag", { hasText: "天文館" })).toBeVisible();
});

test("下書き保存→03の下書き行に現れる(C5)", async ({ page }) => {
  await page.goto("/");
  await page.locator("#intentText").fill("天文館で明日のランチを3人で");
  await expect(page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("#draftButton").click();
  await expect(page.getByText("下書きを保存しました")).toBeVisible();
  await page.goto("/#/standby");
  await expect(page.getByRole("heading", { name: "下書き" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: /を直して預ける/ })).toBeVisible();
});
