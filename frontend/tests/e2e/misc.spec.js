// G-16: 不正・存在しないlatch uuid(G8)。G-17: ≤720pxビューポート実描画(J3)。
import { test, expect } from "./helpers.js";
import { cleanupAll } from "./helpers.js";
import { randomUUID } from "node:crypto";

test.afterAll(cleanupAll);

test("不正uuidのlatch URLはhomeへ戻され、存在しないuuidは404表示(G8)", async ({ page }) => {
  await page.goto("/#/latches/not-a-uuid");
  await expect(page.locator("#homeScreen")).toBeVisible();
  await page.goto(`/#/latches/${randomUUID()}`);
  await expect(page.getByText("提案が見つかりません")).toBeVisible({ timeout: 15_000 });
});

test("未知ルートはhomeへ落ちる(J2の実E2E追い込み)", async ({ page }) => {
  await page.goto("/#/unknown/route");
  await expect(page.locator("#homeScreen")).toBeVisible();
});

test("390pxビューポートで下部tabbarが実描画される(J3)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".tabbar")).toBeVisible();
  await expect(page.locator(".tabbar [data-nav]").first()).toBeVisible();
});
