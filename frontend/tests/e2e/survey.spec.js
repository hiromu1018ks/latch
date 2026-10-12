// A-06(backlog §3・V1-P3): 体感アンケート(05 v0.7・09 §2.3)の実機E2E。
// ①onboarding配信=初回Intent active化後に03待機中の受動カードへ1問(便利さ)が
// 出る→選択→送信→済み表示→再訪で二度と出ない(単発)をDB側証明つきで往復。
// ②未配信=Intentを持たないユーザー(とdraftだけのユーザー)には出ない。
// ③week4=初回回答をSQL過去化(29日前)して4週後ラウンドを駆動(期限系E2Eと
// 同手法)→3問(便利さ+継続意向+任意自由記述)→送信→answers/free_commentを
// DB側証明。配信契機・通知なし(モーダル・バッジなし)は05 v0.7の規定。
import { test, expect } from "./helpers.js";
import { api, cleanupAll, createIntent, provisionUser, sql } from "./helpers.js";

test.afterAll(cleanupAll);

const surveyCount = ({ userId, round } = {}) => {
  const conds = [`user_id='${userId}'::uuid`];
  if (round) conds.push(`round='${round}'`);
  return Number(sql(`SELECT count(*) FROM survey_responses WHERE ${conds.join(" AND ")};`));
};

test("onboarding配信: active化後に1問→送信→済み→再訪で出ない(単発)", async ({ openAuthed }) => {
  const user = await provisionUser({ tag: "svy" });
  await createIntent(user); // 初回Intentのactive化=配信契機1

  const mine = await openAuthed(user, "/#/standby");
  await expect(mine.page.getByText("条件が揃うのを、")).toBeVisible({ timeout: 15_000 });

  // 受動カード: onboardingタイトル+便利さ1問(継続意向・自由記述は出ない)
  const card = mine.page.locator('[data-role="survey"]');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card).toHaveAttribute("data-sv-round", "onboarding");
  await expect(card.getByText("はじめての待ちかた、いかがでしたか?")).toBeVisible();
  await expect(card.getByText("条件を預けて待つだけでLATCH候補が届く仕組みを、便利だと思いますか?")).toBeVisible();
  await expect(card.getByText("今後もLATCHを使い続けたいと思いますか?")).toHaveCount(0);
  expect(await card.locator("[data-sv-comment]").count()).toBe(0);

  // 未選択では送信不可→選択4→送信→済み表示
  await expect(card.locator("[data-sv-submit]")).toBeDisabled();
  await card.locator('[data-sv-option="4"]').click();
  await card.locator("[data-sv-submit]").click();
  await expect(card.getByText("回答を送りました")).toBeVisible({ timeout: 15_000 });

  // DB: onboarding行1件(convenience=4)
  expect(surveyCount({ userId: user.userId, round: "onboarding" })).toBe(1);
  expect(sql(`SELECT answers FROM survey_responses WHERE user_id='${user.userId}'::uuid AND round='onboarding';`)).toContain('"convenience": 4');

  // 再訪でカードは出ない(単発・サーバ=真物)
  await mine.page.reload();
  await expect(mine.page.getByText("条件が揃うのを、")).toBeVisible({ timeout: 15_000 });
  await expect(mine.page.locator('[data-role="survey"]')).toHaveCount(0);
  await mine.close();
});

test("未配信: Intentを持たないユーザーとdraftだけのユーザーには出ない", async ({ openAuthed }) => {
  const noIntent = await provisionUser({ tag: "svy" });
  const draftOnly = await provisionUser({ tag: "svy" });
  await createIntent(draftOnly, { status: "draft" }); // draftは保存でありactive化でない

  for (const user of [noIntent, draftOnly]) {
    const mine = await openAuthed(user, "/#/standby");
    await expect(mine.page.getByText("条件が揃うのを、")).toBeVisible({ timeout: 15_000 });
    await mine.page.waitForTimeout(300); // GET survey応答の描画を待つ
    await expect(mine.page.locator('[data-role="survey"]')).toHaveCount(0);
    await mine.close();
  }
});

test("week4配信: 初回回答29日前のSQL過去化→3問→送信→answers/free_comment", async ({ openAuthed }) => {
  const user = await provisionUser({ tag: "svy" });
  await createIntent(user);
  const answered = await api("POST", "/v1/users/me/survey", {
    token: user.access,
    body: { round: "onboarding", answers: { convenience: 3 } },
  });
  expect(answered.status).toBe(201);
  // 初回回答を29日前へ(4週=28日超えの駆動・期限系E2Eと同じSQL手法)
  sql(`UPDATE survey_responses SET created_at = now() - interval '29 days' WHERE user_id='${user.userId}'::uuid AND round='onboarding';`);

  const mine = await openAuthed(user, "/#/standby");
  await expect(mine.page.getByText("条件が揃うのを、")).toBeVisible({ timeout: 15_000 });

  // week4カード: 3問構成(便利さ+継続意向+任意の自由記述)
  const card = mine.page.locator('[data-role="survey"]');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card).toHaveAttribute("data-sv-round", "week4");
  await expect(card.getByText("4週間、待ってみていかがでしたか?")).toBeVisible();
  await expect(card.getByText("今後もLATCHを使い続けたいと思いますか?")).toBeVisible();
  const comment = card.locator("[data-sv-comment]");
  await expect(comment).toBeVisible();

  // 両方の選択が必須(便利さだけでは送信不可)
  await card.locator('[name="sv-convenience"][value="5"]').check();
  await expect(card.locator("[data-sv-submit]")).toBeDisabled();
  await card.locator('[name="sv-continuation"][value="4"]').check();
  await expect(card.locator("[data-sv-submit]")).toBeEnabled();
  await comment.fill("待っているだけで届くのが楽しい");
  await card.locator("[data-sv-submit]").click();
  await expect(card.getByText("回答を送りました")).toBeVisible({ timeout: 15_000 });

  // DB: week4行(continuation=4・free_comment)
  expect(surveyCount({ userId: user.userId, round: "week4" })).toBe(1);
  const row = sql(`SELECT answers, free_comment FROM survey_responses WHERE user_id='${user.userId}'::uuid AND round='week4';`);
  expect(row).toContain('"convenience": 5');
  expect(row).toContain('"continuation": 4');
  expect(row).toContain("待っているだけで届くのが楽しい");
  await mine.close();
});
