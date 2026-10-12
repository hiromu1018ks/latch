// E-01(backlog §1・S-2解消): Cloudflare実装(cf-dev=miniflare 8787)へのUI E2E。
// 参照実装(ci compose)と対になる「デプロイ経路」の実API×実UI結合。API契約面は
// 契約130ケースが既に担保するため、ここではUI結合の核心のみ:
//  ① 認証+同意のUI経路(トークンパネル→オンボーディング→同意→通常画面)
//  ② 主幹作成(stub parse→02行編集→預ける→03)
//  ③ 実パイプラインchaーん(2ユーザーUI預け入れ→Queues→提案→04最小形→YES×2→成立)
//     = P-01のCloudflare実装版(stub JEVはnoul0.90固定で提案が成立する)。
import { test, expect, provisionUser, futureTimeText, seedStorage, cleanupAll } from "../e2e/helpers.js";

test.setTimeout(240_000);
test.afterAll(cleanupAll);

test("cf実装: 未同意ユーザーがオンボーディングで同意し主幹作成まで通る", async ({ page }) => {
  const user = await provisionUser({ tag: "cf", consent: false });
  await seedStorage(page.context(), user);
  await page.goto("/#/");
  await expect(page).toHaveURL(/#\/onboarding/, { timeout: 15_000 });
  await expect(page.getByText("LLM送信の同意")).toBeVisible();
  await page.getByLabel("上記に同意します").check();
  await page.getByRole("button", { name: "同意する" }).click();
  await expect(page).toHaveURL(/#\/$/, { timeout: 15_000 });
  await expect(page.locator("#intentText")).toBeVisible();

  // 主幹: stub parse→02行編集→預ける→03着地
  const timeText = futureTimeText(2);
  await page.locator("#intentText").fill(`天文館で ${timeText} から 2人で tea`);
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
  await page.locator("#depositButton").click();
  await expect(page).toHaveURL(/#\/standby\?deposited=1/, { timeout: 15_000 });
});

test("cf実装: 2ユーザーUI預け入れ→実Queues評価→04最小形→YES×2→成立(P-01 cf版)", async ({ openAuthed }) => {
  const userA = await provisionUser({ tag: "cfp" });
  const userB = await provisionUser({ tag: "cfp" });
  // +7hスロット=同意テスト(+2h)や手動検証の残存Intentと3時間窓で交差しない
  const timeText = futureTimeText(7);
  const rawText = `天文館で ${timeText} から 2人で tea`;

  const deposit = async (page) => {
    await page.goto("/#/");
    await page.locator("#intentText").fill(rawText);
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
    await page.locator("#depositButton").click();
    await expect(page).toHaveURL(/#\/standby\?deposited=1/, { timeout: 15_000 });
  };

  const a = await openAuthed(userA, "/#/");
  await deposit(a.page);
  const b = await openAuthed(userB, "/#/");
  await deposit(b.page);

  // Queues即時経路+評価(実測~5s・cron */5フォールバック込みで余裕150s)
  let latch = null;
  const started = Date.now();
  while (!latch && Date.now() - started < 150_000) {
    const r = await fetch("http://127.0.0.1:8787/v1/latches", {
      headers: { Authorization: `Bearer ${userA.access}` },
    });
    const data = await r.json().catch(() => null);
    latch = (data?.items ?? []).find((i) => i.status === "proposed");
    if (!latch) await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  expect(latch, "cf実装で提案が形成される").toBeTruthy();
  expect(latch.proposal).toEqual({ headcount: 2, match_level: expect.any(String) });

  // A: 04最小形(照合表なし)→YES→回答待ち
  await a.page.goto("/#/proposals");
  await a.page.locator("#proposalsScreen a.sb-row-link").first().click();
  await expect(a.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(a.page.locator('[data-role="pv-fit"]')).toHaveCount(0);
  await a.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(a.page.getByText(/待っています/)).toBeVisible({ timeout: 15_000 });

  // B: 04→YES→全員参加→チャット→成立
  await b.page.goto("/#/proposals");
  await b.page.locator("#proposalsScreen a.sb-row-link").first().click();
  await b.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(b.page.getByText("全員が参加しました。")).toBeVisible({ timeout: 15_000 });
  await b.page.getByRole("button", { name: "チャットを開く" }).click();
  await expect(b.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await b.close();
  await a.close();
});
