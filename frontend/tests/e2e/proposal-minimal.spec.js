// U-06(backlog §5): hidden_until_match最小形04画面の実機E2E(P0・受け入れ#17・原則8)。
// proposal={headcount,match_level}のみ(UIはhidden_until_match確定送信のため実パイプ
// ラインの正規生成物)のlatchを開き、照合表なし・期限/必要人数/一致度案内のみの
// 「実運用の既定姿」をassertする。全フィールド形(summary_only)との2値比較はU-07。
// 実パイプライン経由の最小形はe2e-staging/pipeline-ui.spec.jsが別途証明する。
import { test, expect } from "./helpers.js";
import { cleanupAll, pairLatchFixture } from "./helpers.js";

test.afterAll(cleanupAll);

test("最小形04: 照合表・4情報カードなし・期限/必要人数/一致度案内は出る(受け入れ#17)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({
    status: "proposed",
    proposal: { headcount: 2, match_level: "medium" },
  });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);

  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  // 最小形の知らせ(条件が合う候補があります+「全員が参加するまでくわしくは出ない」注記)
  await expect(mine.page.getByText(/条件が合う候補があります/).first()).toBeVisible();
  await expect(
    mine.page.getByText("くわしい内容は、全員が「参加する」と答えたときにわかります"),
  ).toBeVisible();

  // 照合表(pv-fit)・4情報カード(pv-cards)は出ない — 表示されないことが本体
  await expect(mine.page.locator('[data-role="pv-fit"]')).toHaveCount(0);
  await expect(mine.page.locator(".pv-cards")).toHaveCount(0);

  // 必要人数(まだ見えない1人)・回答期限は出る(一致度の生スコアは最小形では
  // 非開示が正しい — match_levelの高/中/低も04最小形には出ない)
  await expect(mine.page.locator('[data-role="who"]')).toHaveText("あなた ＋ まだ見えない1人");
  await expect(mine.page.getByText("回答期限まで")).toBeVisible();

  // 回答2択(YES)は最小形でも可能
  await expect(mine.page.locator('[data-role="respond"][data-value="yes"]')).toBeEnabled();
  await mine.close();
});
