// G-04: LLM同意ゲートの実フロー(B1/B2/B3)。Appの発火配線(起動時誘導・422横断
// 検出ラップ)がunit未証明のため、本E2Eが本丸。
import { test, expect } from "@playwright/test";
import { provisionUser, seedStorage, cleanupAll } from "./helpers.js";

test.afterAll(cleanupAll);

test("未同意ユーザーは起動時にオンボーディングへ誘導される(B1)", async ({ page }) => {
  const user = await provisionUser({ consent: false });
  await seedStorage(page.context(), user);
  await page.goto("/");
  await expect(page).toHaveURL(/#\/onboarding/);
  await expect(page.getByText("LLM送信の同意")).toBeVisible();
});

test("未同意ユーザーのparseは422 CONSENT_REQUIREDでトースト+誘導される(B2)", async ({ page }) => {
  const user = await provisionUser({ consent: false });
  await seedStorage(page.context(), user);
  await page.goto("/#/settings");
  // 起動時ゲート(B1)の書き換えを待ってからhomeへ戻る(クリックとゲートの競合を避ける)
  await expect(page).toHaveURL(/#\/onboarding/, { timeout: 15_000 });
  await page.getByRole("link", { name: "預ける" }).click();
  await expect(page.locator("#intentText")).toBeVisible();
  await page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  await expect(page.getByText("マッチングの利用には同意が必要です。同意画面へ移動します。")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page).toHaveURL(/#\/onboarding/);
});

test("オンボーディングで同意すると通常利用に戻り、再訪でも誘導されない(B3)", async ({ page }) => {
  const user = await provisionUser({ consent: false });
  await seedStorage(page.context(), user);
  await page.goto("/");
  await expect(page).toHaveURL(/#\/onboarding/);
  await page.getByLabel("上記に同意します").check();
  await page.getByRole("button", { name: "同意する" }).click();
  await expect(page.getByText("同意を記録しました")).toBeVisible();
  await expect(page).toHaveURL(/#\/$/);
  await expect(page.locator("#intentText")).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/#\/$/);
  await expect(page.locator("#intentText")).toBeVisible();
});
