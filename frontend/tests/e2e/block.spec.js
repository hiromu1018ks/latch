// U-02(backlog §5): ブロックdialog(H10)の実機E2E(受け入れ#20 UI面)。
// U-01(通報dialog・safety.spec.js)とG-14(ブロック解除・errors-and-settings.spec.js)
// の間を埋める — 05成立画面起点で「ブロックする」→POST→完了表示→設定の
// ブロック一覧へ反映までを実APIで通す。POST /v1/users/{id}/blockに取得APIが
// あるため一覧反映はUIで、投入実績はblocks表でも証明する(G-14はAPI直叩きで
// 先にブロックを作っていたため、UI起点の投入は本specが初めて)。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture, sql } from "./helpers.js";

test.afterAll(cleanupAll);

test("成立画面からブロックする→完了表示→設定のブロック一覧へ反映(H10・受け入れ#20 UI面)", async ({ openAuthed }) => {
  const { userA, userB, latchId } = await pairLatchFixture({ status: "matched", tag: "u02" });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  // 05の「安全のために」→「通報・ブロック」で統合dialogを開く
  await mine.page.getByRole("button", { name: "通報・ブロック" }).click();
  const dlg = mine.page.getByRole("dialog", { name: "通報・ブロック" });
  await expect(dlg).toBeVisible();
  // 1対1成立は対象=相手のみ・あらかじめ選択済み(U-01と同一形状)
  const radio = dlg.locator('input[name="safety-who"]');
  await expect(radio).toHaveCount(1);
  await expect(radio).toBeChecked();

  // 「ブロックする」→POST → 完了表示(相手名+今後LATCHが成立しない旨)
  await dlg.getByRole("button", { name: "ブロックする" }).click();
  await expect(
    dlg.getByText("E2E u02さんをブロックしました。今後この相手とLATCHが成立することはありません。"),
  ).toBeVisible({ timeout: 15_000 });

  // 投入実績: blocks表にblocker=自分・blocked=相手の行
  expect(
    Number(
      sql(
        `SELECT count(*) FROM blocks WHERE blocker_id='${userA.userId}'::uuid AND blocked_id='${userB.userId}'::uuid;`,
      ),
    ),
  ).toBe(1);

  // dialogを閉じて設定画面へ — ブロック一覧に相手が反映されている
  await dlg.getByRole("button", { name: "閉じる" }).click();
  await mine.page.goto("/#/settings");
  await expect(mine.page.locator(".block-name", { hasText: "E2E u02" })).toBeVisible({ timeout: 15_000 });
  // 行の操作(解除)も出ている=G-14の導線と接続している
  await expect(
    mine.page.getByRole("button", { name: "E2E u02さんのブロックを解除" }),
  ).toBeVisible();
  await mine.close();
});
