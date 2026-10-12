// G-07: 成立後チャットの実送受信(H3/H4)。2ブラウザコンテキスト=自分/相手。
// あわせてG-13(.ics実ダウンロード)とG-15(leave→再訪readonly)。
import { test, expect } from "@playwright/test";
import { cleanupAll, pairLatchFixture, openAuthed } from "./helpers.js";

test.afterAll(cleanupAll);

test("成立画面が開き、チャット送信が相手側にも反映される(H1/H3)", async ({ browser }) => {
  const { userA, userB, latchId } = await pairLatchFixture({ status: "matched" });
  const mine = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.getByText("LATCHが成立し、チャットが開きました")).toBeVisible();

  const marker = `こんにちは-${Date.now().toString(36)}`;
  await mine.page.getByPlaceholder("メッセージを送る").fill(marker);
  await mine.page.getByRole("button", { name: "送信" }).click();
  await expect(mine.page.getByText(marker)).toBeVisible();

  // 相手コンテキストで同じlatchを開く=サーバ永続の証明(H3 persist)
  const theirs = await openAuthed(browser, userB, `/#/latches/${latchId}`);
  await expect(theirs.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await expect(theirs.page.getByText(marker)).toBeVisible({ timeout: 15_000 });
  // リロードしても自分側に残る
  await mine.page.reload();
  await expect(mine.page.getByText(marker)).toBeVisible({ timeout: 15_000 });
  await theirs.context.close();
  await mine.context.close();
});

test("カレンダー追加で.icsが実ダウンロードされる(H2/G-13)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({ status: "matched" });
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "カレンダーに追加" }).click(),
  ]);
  const body = (await download.path()) ? (await import("node:fs")).readFileSync(await download.path(), "utf8") : "";
  expect(body).toContain("BEGIN:VCALENDAR");
  expect(body).toContain("DTSTART");
  await context.close();
});

test("参加取り消し(leave)後の再訪では書き込めない(H6/G-15)", async ({ browser }) => {
  const { userA, latchId } = await pairLatchFixture({ status: "matched" });
  const { context, page } = await openAuthed(browser, userA, `/#/latches/${latchId}`);
  await expect(page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "この成立を取り消す" }).click();
  await expect(page.getByText("この成立を取り消しますか？")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "取り消す" }).click();
  await expect(page.getByText("参加を取り消しました。")).toBeVisible();
  // 再訪: サーバのleft状態から読取専用へ収束する(設計上のpersist転嫁先)
  await page.goto("/#/standby");
  await page.goto(`/#/latches/${latchId}`);
  await expect(page.getByRole("button", { name: "送信" })).toBeDisabled({ timeout: 15_000 });
  await context.close();
});
