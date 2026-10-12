// G-05: 回答・undoの実フロー(G1/G2/G4/G5/G6・E6・J1バッジ)。
// G5(undo→open復帰)はunitにアサーション自体が存在しない(FG-07)最重要の新規証明。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture, expireResponseDeadline } from "./helpers.js";

test.afterAll(cleanupAll);

test("提案姿が実APIから描画され、YESで参加状態に遷移する(G1/G2)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.getByText("あなたの条件との照合")).toBeVisible();
  await mine.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(mine.page.getByText("参加と回答しました。")).toBeVisible();
  await expect(mine.page.getByText("全員が参加しました。")).toHaveCount(0);
  await mine.close();
});

test("両者YESの2ブラウザ連鎖で双方の画面が05(成立)へ遷移する(U-05・受け入れ#15)", async ({ openAuthed }) => {
  const { userA, userB, latchId } = await pairLatchFixture({});
  const a = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(a.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await a.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(a.page.getByText("参加と回答しました。")).toBeVisible();
  const b = await openAuthed(userB, `/#/latches/${latchId}`);
  await expect(b.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  // B(第2コンテキスト)のYESが全員参加を確定させる
  await b.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(b.page.getByText("全員が参加しました。")).toBeVisible({ timeout: 15_000 });
  await b.page.getByRole("button", { name: "チャットを開く" }).click();
  await expect(b.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  // Aは回答済みのまま再訪で成立姿(05)へ(同一hashのままではhashchangeが
  // 発火しないため一旦03へ遷移してから開き直す — pipeline-uiと同手法)
  await a.page.goto("/#/standby");
  await a.page.goto(`/#/latches/${latchId}`);
  await expect(a.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await b.close();
  await a.close();
});

test("undoするとopen姿に復帰し、回答2択が再表示される(G5・FG-07の新規証明)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await mine.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(mine.page.getByText("参加と回答しました。")).toBeVisible();
  await mine.page.getByRole("button", { name: "回答を取り消す" }).click();
  // open復帰: 提案ヒーローと2択が戻る(既存unitはここを証明していなかった)
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.locator('[data-role="respond"][data-value="yes"]')).toBeVisible();
  await expect(mine.page.locator('[data-role="respond"][data-value="defer"]')).toBeVisible();
  await mine.close();
});

test("回答済み状態はリロード後も維持される(G2 persist)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await mine.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(mine.page.getByText("参加と回答しました。")).toBeVisible();
  await mine.page.reload();
  await expect(mine.page.getByText("参加と回答しました。")).toBeVisible({ timeout: 15_000 });
  await mine.close();
});

test("見送り+理由chipsでdefer回答を送り待機中へ戻る(G4)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await mine.page.locator('[data-role="respond"][data-value="defer"]').click();
  await expect(mine.page.getByText("見送りました。")).toBeVisible();
  await mine.page.locator('[data-reason="time"]').click();
  await mine.page.locator('[data-role="confirm-decline"]').click();
  await expect(mine.page).toHaveURL(/#\/standby/);
  // サーバ記録済み(my_response=defer)として再訪しても見送り表示
  await mine.page.goto(`/#/latches/${latchId}`);
  await expect(mine.page.getByText("見送りました。")).toBeVisible({ timeout: 15_000 });
  await mine.close();
});

test("回答期限が過ぎた後のundoは409で収束トーストを出す(G6)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await mine.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(mine.page.getByText("参加と回答しました。")).toBeVisible();
  expireResponseDeadline(latchId);
  await mine.page.getByRole("button", { name: "回答を取り消す" }).click();
  await expect(mine.page.getByText("この回答は取り消せませんでした")).toBeVisible({ timeout: 15_000 });
  await mine.close();
});

test("候補があると03着地時にトースト+見る導線とバッジが出る(E6/J1)", async ({ openAuthed }) => {
  const { userA } = await pairLatchFixture({});
  const mine = await openAuthed(userA, "/#/standby");
  await expect(mine.page.locator(".toast-msg", { hasText: "LATCH候補があります" })).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.locator(".count-pill[data-count='proposals']").first()).toBeVisible();
  await mine.page.getByRole("link", { name: "見る" }).click();
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await mine.close();
});
