// G-07: 成立後チャットの実送受信(H3/H4)。2ブラウザコンテキスト=自分/相手。
// あわせてG-13(.ics実ダウンロード)とG-15(leave→再訪readonly)。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture } from "./helpers.js";
import { readFileSync } from "node:fs";

test.afterAll(cleanupAll);

test("成立画面が開き、チャット送信が相手側にも反映される(H1/H3)", async ({ openAuthed }) => {
  const { userA, userB, latchId } = await pairLatchFixture({ status: "matched" });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.getByText("LATCHが成立し、チャットが開きました")).toBeVisible();

  const marker = `こんにちは-${Date.now().toString(36)}`;
  await mine.page.getByPlaceholder("メッセージを送る").fill(marker);
  await mine.page.getByRole("button", { name: "送信" }).click();
  await expect(mine.page.getByText(marker)).toBeVisible();

  // 相手コンテキストで同じlatchを開く=サーバ永続の証明(H3 persist)
  const theirs = await openAuthed(userB, `/#/latches/${latchId}`);
  await expect(theirs.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await expect(theirs.page.getByText(marker)).toBeVisible({ timeout: 15_000 });
  // リロードしても自分側に残る
  await mine.page.reload();
  await expect(mine.page.getByText(marker)).toBeVisible({ timeout: 15_000 });
  await theirs.close();
  await mine.close();
});

test("カレンダー追加で.icsが実ダウンロードされる(H2/G-13)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({ status: "matched" });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  const [download] = await Promise.all([
    mine.page.waitForEvent("download"),
    mine.page.getByRole("button", { name: "カレンダーに追加" }).click(),
  ]);
  const body = (await download.path()) ? readFileSync(await download.path(), "utf8") : "";
  expect(body).toContain("BEGIN:VCALENDAR");
  expect(body).toContain("DTSTART");
  await mine.close();
});

test("参加取り消し(leave)後の再訪では書き込めない(H6/G-15)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({ status: "matched" });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await mine.page.getByRole("button", { name: "この成立を取り消す" }).click();
  await expect(mine.page.getByText("この成立を取り消しますか？")).toBeVisible();
  await mine.page.getByRole("dialog").getByRole("button", { name: "取り消す" }).click();
  await expect(mine.page.getByText("参加を取り消しました。")).toBeVisible();
  // 再訪: サーバのleft状態から読取専用へ収束する(設計上のpersist転嫁先)
  await mine.page.goto("/#/standby");
  await mine.page.goto(`/#/latches/${latchId}`);
  await expect(mine.page.getByRole("button", { name: "送信" })).toBeDisabled({ timeout: 15_000 });
  await mine.close();
});
