// U-04(backlog §5): attendance happyの実機(原則6・D-09)。
// completed fixture→05の出席申告2択→POST→済み状態。窓閉契約(WINDOW_CLOSED)と
// 二重回答(409 ATTENDANCE_ALREADY_SUBMITTED)はvitest H7/H8・backend integration
// (test_chat_attendance_api)で実施済みのため、ここはhappy経路のみ。
// completed fixtureはinsertCompletedLatch(latch行+completed_at+calibration_records)
// で作る — 実運用のcompleted化はsweeper(対象時刻経過)だがUI表示・申告POSTには
// statusとcalibration行だけが効くためfixture直挿入で正当。
import { test, expect } from "./helpers.js";
import { cleanupAll, provisionUser, createIntent, insertCompletedLatch, sql } from "./helpers.js";

test.afterAll(cleanupAll);

test("completed成立の出席申告2択→「会いました」→済み表示(U-04・原則6)", async ({ openAuthed }) => {
  const userA = await provisionUser({ tag: "u04" });
  const userB = await provisionUser({ tag: "u04" });
  const intentA = await createIntent(userA);
  const intentB = await createIntent(userB);
  const latchId = insertCompletedLatch(intentA, intentB);
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  // 出席申告カード: 質問と2択(会いました/会えていません)
  const card = mine.page.locator('[data-role="attendance"]');
  await expect(card.getByText("実際に会いましたか?")).toBeVisible();
  await expect(card.getByRole("button", { name: "会いました" })).toBeVisible();
  await expect(card.getByRole("button", { name: "会えていません" })).toBeVisible();

  // 「会いました」→ POST → 済み表示(2択が消えてnoteだけが残る)
  await card.getByRole("button", { name: "会いました" }).click();
  await expect(card.getByText("回答を送りました")).toBeVisible({ timeout: 15_000 });
  await expect(card.getByRole("button", { name: "会いました" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "会えていません" })).toHaveCount(0);

  // 書込実績: calibration_records.actual_attended=true(LATCH単位・先着1名の書込先)
  expect(sql(`SELECT actual_attended FROM calibration_records WHERE latch_id='${latchId}'::uuid;`)).toBe("t");
  await mine.close();
});
