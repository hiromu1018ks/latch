// G-03: 認証・セッションの実フロー(A1〜A6)。TokenPanelはunitで書けない構造
// (fetch注入不可)のため、本E2Eがパネル挙動の最初の証明になる。
import { test, expect } from "@playwright/test";
import { provisionUser, cleanupAll, api, issueIdpToken } from "./helpers.js";

test.afterAll(cleanupAll);

test("未認証訪問者には画面シェルと開発用トークンパネルが出る(A1)", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("IdPトークン")).toBeVisible();
  await expect(page.locator("#intentText")).toBeVisible();
});

test("有効なIdPトークンで接続するとパネルが閉じてトークンが保存される(A2)", async ({ page }) => {
  const user = await provisionUser({});
  await page.goto("/");
  await page.getByLabel("IdPトークン").fill(user.idpToken);
  await page.getByRole("button", { name: "接続する" }).click();
  await expect(page.getByLabel("IdPトークン")).toBeHidden();
  const access = await page.evaluate(() => sessionStorage.getItem("latch-access"));
  const refresh = await page.evaluate(() => localStorage.getItem("latch-refresh"));
  expect(access).toBeTruthy();
  expect(refresh).toBeTruthy();
});

test("無効なIdPトークンは「トークンが無効です」で再入力を促す(A3)", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("IdPトークン").fill("invalid.token.value");
  await page.getByRole("button", { name: "接続する" }).click();
  await expect(page.getByText("トークンが無効です。再発行して貼り直してください。")).toBeVisible();
});

test("API到達不能時は「接続できませんでした」を出す(A4)", async ({ page }) => {
  await page.goto("/");
  await page.route("**/v1/auth/token", (route) => route.abort());
  await page.getByLabel("IdPトークン").fill(issueIdpToken("e2e-abort"));
  await page.getByRole("button", { name: "接続する" }).click();
  await expect(page.getByText("接続できませんでした。APIの起動を確認してください。")).toBeVisible();
});

// findings#1(並行refresh競合で族失効)はclient.jsの単一フライト化で修正済み。
// 本テストは新規タブ再訪(accessなし+refresh保持)の正規経路を実APIで証明する。
test("access切れ+refresh保持の再訪は自動refreshでAPIが継続する(A5)", async ({ page }) => {
  const user = await provisionUser({});
  await page.context().addInitScript(([r]) => localStorage.setItem("latch-refresh", r), [user.refresh]);
  await page.goto("/#/standby");
  await expect(page.getByText("条件が揃うのを、")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByLabel("IdPトークン")).toBeHidden();
});

test("refreshも失効しているとセッション破棄してパネルへ戻る(A6)", async ({ page }) => {
  await page.context().addInitScript(() => {
    sessionStorage.setItem("latch-access", "garbage.token.value");
    localStorage.setItem("latch-refresh", "garbage-refresh");
  });
  await page.goto("/#/standby");
  await expect(page.getByLabel("IdPトークン")).toBeVisible({ timeout: 15_000 });
});

test("未登録のIdPトークンでも交換自体は成功する(登録導線なしの現仕様)", async ({ page }) => {
  const exchanged = await api("POST", "/v1/auth/token", {
    body: { provider: "google", idp_token: issueIdpToken("e2e-unregistered-probe") },
  });
  expect(exchanged.status).toBe(200);
  expect(exchanged.data.access_token).toBeTruthy();
});
