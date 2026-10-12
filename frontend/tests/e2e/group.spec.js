// P-02(backlog §2): グループLATCH(3人)のUI(受け入れ#18)。04の必要人数/残回答
// 表示(partial_accept)・全員YES成立・05の複数人表示を実APIの回答経路で通す。
// 通報対象ラジオ2人はU-01(safety.spec.js)で・グループのAPI面(6検証)はstaging
// G3で実証済みのため対象外。fixtureはinsertGroupLatch(group_candidates+latches
// 直接INSERT・契約harnessと同じ手法)でproposed状態から始め、3人のYESはすべて
// 実UIの回答ボタンから送る。
import { test, expect } from "./helpers.js";
import {
  cleanupAll,
  provisionUser,
  createIntent,
  insertGroupLatch,
  sql,
} from "./helpers.js";

test.afterAll(cleanupAll);

test("グループ3人: 04の必要人数→YESごとの残回答(partial_accept)→全員YES成立→05に3人(受け入れ#18)", async ({ openAuthed }) => {
  const users = [
    await provisionUser({ tag: "grpA" }),
    await provisionUser({ tag: "grpB" }),
    await provisionUser({ tag: "grpC" }),
  ];
  const intents = [];
  for (const u of users) intents.push(await createIntent(u));
  const { latchId } = insertGroupLatch(intents, { status: "proposed" });

  // Aの04: 3人必要(まだ見えない2人)→YES→回答待ち2人(1人目のpartial_accept)
  const a = await openAuthed(users[0], `/#/latches/${latchId}`);
  await expect(a.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(a.page.locator('[data-role="who"]')).toHaveText("あなた ＋ まだ見えない2人");
  await a.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(a.page.getByText("参加と回答しました。")).toBeVisible();
  await expect(a.page.locator('[data-role="who"]')).toHaveText("あなた（参加）＋ 回答待ちの2人");
  await expect(
    a.page.getByText("ほかの2人の回答を待っています。全員がそろったら、お知らせします。"),
  ).toBeVisible();

  // Bの04: YES→残り1人(2人目のpartial_accept・1人が回答済み)
  const b = await openAuthed(users[1], `/#/latches/${latchId}`);
  await expect(b.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await b.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(
    b.page.getByText("ほかの2人の回答を待っています（1人が回答済み）。全員がそろったら、お知らせします。"),
  ).toBeVisible();

  // Aの再訪: 残回答の現況(1人が回答済み)が見える(同hash再訪はhashchangeしないため03経由)
  await a.page.goto("/#/standby");
  await a.page.goto(`/#/latches/${latchId}`);
  await expect(
    a.page.getByText("ほかの2人の回答を待っています（1人が回答済み）。全員がそろったら、お知らせします。"),
  ).toBeVisible();

  // CのYESで全員参加→04のallIn「3人全員が参加しました。」→チャットを開く→05
  const c = await openAuthed(users[2], `/#/latches/${latchId}`);
  await expect(c.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await c.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(c.page.getByText("3人全員が参加しました。")).toBeVisible();
  await c.page.getByRole("button", { name: "チャットを開く" }).click();
  await expect(c.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  // 05の複数人表示: ヒーローは3人全員・メンバーカードに自分以外2人+あなた
  await expect(c.page.getByText("3人全員が「参加する」と答えました。")).toBeVisible();
  await expect(c.page.locator(".lt-member-name", { hasText: "E2E grpA" })).toHaveCount(1);
  await expect(c.page.locator(".lt-member-name", { hasText: "E2E grpB" })).toHaveCount(1);
  await expect(c.page.locator(".lt-member-name", { hasText: "あなた" })).toHaveCount(1);
  await expect(c.page.locator(".lt-member-name")).toHaveCount(3);

  // 成立の実績: latch=matched・3人のIntentがmatched化・成立通知3行(D-20同時送信)
  expect(sql(`SELECT status FROM latches WHERE id='${latchId}'::uuid;`)).toBe("matched");
  const uuidList = intents.map((id) => `'${id}'::uuid`).join(",");
  expect(
    Number(sql(`SELECT count(*) FROM intents WHERE id = ANY(ARRAY[${uuidList}]) AND status='matched';`)),
  ).toBe(3);
  expect(
    Number(sql(`SELECT count(*) FROM notifications WHERE type='latch_matched' AND payload->>'latch_id'='${latchId}';`)),
  ).toBe(3);

  // Aの再訪でも05(3人)へ
  await a.page.goto("/#/standby");
  await a.page.goto(`/#/latches/${latchId}`);
  await expect(a.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  await expect(a.page.locator(".lt-member-name", { hasText: "E2E grpB" })).toHaveCount(1);
  await expect(a.page.locator(".lt-member-name", { hasText: "E2E grpC" })).toHaveCount(1);

  await c.close();
  await b.close();
  await a.close();
});
