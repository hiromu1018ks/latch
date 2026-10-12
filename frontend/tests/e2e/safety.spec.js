// U-01(backlog §5): 通報・ブロック統合dialogの実機E2E。docs/02の検証方法が唯一
// UI操作を明記するSafety項目(#21)なのにE2EがゼロだったP0。
// 3つの開き方×dialog形状を網羅:
//  1対1成立: 対象ラジオ=相手1人(固定checked)→理由4択→POST(reportee=相手)
//  グループ成立: 対象ラジオ=2人(選択必須)→理由→POST(reportee=選択者)
//  1対1提案(04): participants非開示=対象ラジオなし(reportee_id省略の案X)
// POST /v1/reportsは受付のみで取得APIがないため、投入実績はDB側で証明する。
import { test, expect } from "./helpers.js";
import {
  cleanupAll,
  pairLatchFixture,
  insertGroupLatch,
  provisionUser,
  createIntent,
  reportsCount,
} from "./helpers.js";

test.afterAll(cleanupAll);

test("1対1成立: 安全のために→通報・ブロック→対象固定→理由4択→通報完了(H9)", async ({ openAuthed }) => {
  const { userA, userB, latchId } = await pairLatchFixture({ status: "matched" });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  await mine.page.getByRole("button", { name: "通報・ブロック" }).click();
  const dlg = mine.page.getByRole("dialog", { name: "通報・ブロック" });
  await expect(dlg).toBeVisible();
  // 1対1は対象=相手のみ・あらかじめ選択済み(radioはcheckedで出る)
  const radio = dlg.locator('input[name="safety-who"]');
  await expect(radio).toHaveCount(1);
  await expect(radio).toBeChecked();

  await dlg.getByRole("button", { name: "通報する" }).click();
  // 理由4択(JIS水準のラジオ)・未選択では送信できない
  const reasons = dlg.locator('input[name="safety-reason"]');
  await expect(reasons).toHaveCount(4);
  await expect(dlg.getByRole("button", { name: "通報する" })).toBeDisabled();
  await dlg.locator("label.safety-opt", { hasText: "不適切な内容" }).locator("input").check();
  await dlg.getByRole("button", { name: "通報する" }).click();
  await expect(dlg.getByText("通報を受け付けました。運営が内容を確認します。")).toBeVisible();

  // 投入実績: reporter=自分・reportee=相手・reasonコード
  expect(
    reportsCount({ reporterId: userA.userId, reporteeId: userB.userId, latchId }),
  ).toBe(1);
  expect(reportsCount({ latchId })).toBe(1);
  await mine.close();
});

test("グループ成立: 対象ラジオ2人から選択して通報する(H9・人数>2の対象開示)", async ({ openAuthed }) => {
  const users = [
    await provisionUser({ tag: "grp" }),
    await provisionUser({ tag: "grp" }),
    await provisionUser({ tag: "grp" }),
  ];
  const intents = [];
  for (const u of users) intents.push(await createIntent(u));
  const { latchId } = insertGroupLatch(intents, { status: "matched" });
  const mine = await openAuthed(users[0], `/#/latches/${latchId}`);
  await expect(mine.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  await mine.page.getByRole("button", { name: "通報・ブロック" }).click();
  const dlg = mine.page.getByRole("dialog", { name: "通報・ブロック" });
  await expect(dlg).toBeVisible();
  // グループは対象ラジオ=自分以外の2人・未選択だと通報に進めない
  const radios = dlg.locator('input[name="safety-who"]');
  await expect(radios).toHaveCount(2);
  await expect(dlg.getByRole("button", { name: "通報する" })).toBeDisabled();
  await radios.nth(1).check();
  await dlg.getByRole("button", { name: "通報する" }).click();
  await dlg.locator("label.safety-opt", { hasText: "なりすまし疑い" }).locator("input").check();
  await dlg.getByRole("button", { name: "通報する" }).click();
  await expect(dlg.getByText("通報を受け付けました。運営が内容を確認します。")).toBeVisible();

  // 選択した2人目(users[2])がreporteeになる
  expect(reportsCount({ reporterId: users[0].userId, reporteeId: users[2].userId, latchId })).toBe(1);
  await mine.close();
});

test("1対1提案(04): 「この提案を通報する」は対象非開示のまま通報できる(案X)", async ({ openAuthed }) => {
  const { userA, latchId } = await pairLatchFixture({ status: "proposed" });
  const mine = await openAuthed(userA, `/#/latches/${latchId}`);
  await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });

  await mine.page.getByRole("button", { name: "この提案を通報する" }).click();
  const dlg = mine.page.getByRole("dialog", { name: "通報・ブロック" });
  await expect(dlg).toBeVisible();
  // 提案1対1はparticipants非開示: 対象ラジオなし・ブロック導線なし
  await expect(dlg.locator('input[name="safety-who"]')).toHaveCount(0);
  await expect(dlg.getByRole("button", { name: "ブロックする" })).toHaveCount(0);
  // 通報のみ可能(reportee_id省略body)
  const reportBtn = dlg.getByRole("button", { name: "通報する" });
  await expect(reportBtn).toBeEnabled();
  await reportBtn.click();
  await dlg.locator("label.safety-opt", { hasText: "その他" }).locator("input").check();
  await dlg.getByRole("button", { name: "通報する" }).click();
  await expect(dlg.getByText("通報を受け付けました。運営が内容を確認します。")).toBeVisible();

  // reportee_idなしの投入(reporterのみ特定できる)
  expect(reportsCount({ reporterId: userA.userId, latchId })).toBe(1);
  expect(reportsCount({ latchId })).toBe(1);
  await mine.close();
});
