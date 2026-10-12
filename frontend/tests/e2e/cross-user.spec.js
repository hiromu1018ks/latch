// G-01(P0): 他ユーザーのlatchへの横断アクセス。実セッション2名でのUI結合証明
// (API面の遮断は契約試験のoutsiderケースが担保済み)。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture } from "./helpers.js";

test.afterAll(cleanupAll);

test("関与しないユーザーがlatch URLを直叩きすると404表示+ホーム導線(G7)", async ({ page }) => {
  const { userA, userB, latchId } = await pairLatchFixture({});
  expect(userA.userId).toBeTruthy();
  expect(userB.userId).toBeTruthy();
  // page は 第三者(user fixture)のセッション。latchはA/Bのみに関与。
  await page.goto(`/#/latches/${latchId}`);
  await expect(page.getByText("提案が見つかりません")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "ホームへ戻る" })).toBeVisible();
  await expect(page.getByText("条件が、揃いそうです。")).toHaveCount(0);
});

test("自分のlatch URLは提案姿として開ける(対照・G1実E2E)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({});
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.getByRole("button", { name: "参加する" })).toBeVisible();
  await mine.close();
});
