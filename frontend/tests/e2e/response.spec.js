// G-05: 回答・undoの実フロー(G1/G2/G4/G5/G6・E6・J1バッジ)。
// G5(undo→open復帰)はunitにアサーション自体が存在しない(FG-07)最重要の新規証明。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture, expireResponseDeadline, openAuthed } from "./helpers.js";

test.afterAll(cleanupAll);

test("提案姿が実APIから描画され、YESで参加状態に遷移する(G1/G2)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("あなたの条件との照合")).toBeVisible();
  await page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(page.getByText("参加と回答しました。")).toBeVisible();
  await expect(page.getByText("全員が参加しました。")).toHaveCount(0);
  await context.close();
});

test("undoするとopen姿に復帰し、回答2択が再表示される(G5・FG-07の新規証明)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(page.getByText("参加と回答しました。")).toBeVisible();
  await page.getByRole("button", { name: "回答を取り消す" }).click();
  // open復帰: 提案ヒーローと2択が戻る(既存unitはここを証明していなかった)
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-role="respond"][data-value="yes"]')).toBeVisible();
  await expect(page.locator('[data-role="respond"][data-value="defer"]')).toBeVisible();
  await context.close();
});

test("回答済み状態はリロード後も維持される(G2 persist)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(page.getByText("参加と回答しました。")).toBeVisible();
  await page.reload();
  await expect(page.getByText("参加と回答しました。")).toBeVisible({ timeout: 15_000 });
  await context.close();
});

test("見送り+理由chipsでdefer回答を送り待機中へ戻る(G4)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await page.locator('[data-role="respond"][data-value="defer"]').click();
  await expect(page.getByText("見送りました。")).toBeVisible();
  await page.locator('[data-reason="time"]').click();
  await page.locator('[data-role="confirm-decline"]').click();
  await expect(page).toHaveURL(/#\/standby/);
  // サーバ記録済み(my_response=defer)として再訪しても見送り表示
  await page.goto(`/#/latches/${latchId}`);
  await expect(page.getByText("見送りました。")).toBeVisible({ timeout: 15_000 });
  await context.close();
});

test("回答期限が過ぎた後のundoは409で収束トーストを出す(G6)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(page.getByText("参加と回答しました。")).toBeVisible();
  expireResponseDeadline(latchId);
  await page.getByRole("button", { name: "回答を取り消す" }).click();
  await expect(page.getByText("この回答は取り消せませんでした")).toBeVisible({ timeout: 15_000 });
  await context.close();
});

test("候補があると03着地時にトースト+見る導線とバッジが出る(E6/J1)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const { context, page } = await openAuthed(browser, userA, "/#/standby");
  await expect(page.locator(".toast-msg", { hasText: "LATCH候補があります" })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".count-pill[data-count='proposals']").first()).toBeVisible();
  await page.getByRole("link", { name: "見る" }).click();
  await expect(page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await context.close();
});
