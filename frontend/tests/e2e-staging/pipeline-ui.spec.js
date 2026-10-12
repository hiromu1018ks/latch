// P-01(backlog §2・S-1解消): UI作成Intent→実workerパイプライン→04画面→YES→成立の
// フルチェーン。Playwright 36件のlatchはSQL直挿入でworkerを経由せず・staging S0〜S5
// はUIなし、という両軸の穴を初めて1本につなぐ。
// あわせてU-06実パイプライン版: UIはvisibility=hidden_until_match確定送信のため
// 実パイプラインのproposalは{headcount,match_level}のみ=最小形04が実運用の既定姿。
// E-03(backlog §1): staging(nginx 18000)へのUI E2E接続の本体証明でもある
// (vite proxy 5174→18000・Clock=実時間・stub Jev noul0.90=スコア0.90≥0.60)。
import { test, expect, provisionUser, cleanupAll, futureTimeText, api } from "../e2e/helpers.js";
import {
  ensureStagingClockReal,
  waitForLatch,
  depositViaUi,
} from "./helpers.js";

test.beforeAll(() => {
  ensureStagingClockReal();
});
test.afterAll(cleanupAll);

test.setTimeout(300_000);

test("2ユーザーUI預け入れ→実パイプライン提案→双方YES→成立チャット(P-01/U-06)", async ({ openAuthed }) => {
  const userA = await provisionUser({ tag: "p01" });
  const userB = await provisionUser({ tag: "p01" });
  // 時刻は両ユーザーで同一値(stub Parserは固定応答のため02の行編集で揃える)。
  // +2hスロット=通知テスト(+6h)と3時間窓で交差しない
  const timeText = futureTimeText(2);
  const rawText = `天文館で ${timeText} から 2人でごはん`;

  const a = await openAuthed(userA, "/#/");
  await depositViaUi(a.page, { text: rawText, timeText });
  const b = await openAuthed(userB, "/#/");
  await depositViaUi(b.page, { text: rawText, timeText });

  // 実worker: debounce 10s+tick≤60sでlatch昇格(実測~15s・余裕180s)
  const latch = await waitForLatch(userA);
  expect(latch.status).toBe("proposed");
  // UI確定送信のvisibility=hidden_until_match→build_proposal最小形(-U-06実配線)
  expect(latch.proposal).toEqual({ headcount: 2, match_level: expect.any(String) });

  // A: 候補一覧→04最小形(照合表なし・非開示注記・まだ見えない1人)
  await a.page.goto("/#/proposals");
  await expect(a.page.locator("#proposalsScreen").getByText(/条件が合う候補/).first()).toBeVisible({
    timeout: 15_000,
  });
  await a.page.locator("#proposalsScreen a.sb-row-link").first().click();
  await expect(a.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(a.page.locator('[data-role="pv-fit"]')).toHaveCount(0);
  await expect(
    a.page.getByText("くわしい内容は、全員が「参加する」と答えたときにわかります"),
  ).toBeVisible();
  await expect(a.page.locator('[data-role="who"]')).toHaveText("あなた ＋ まだ見えない1人");

  // A: YES→「回答待ち」(joined)へ
  await a.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(a.page.getByText(/待っています/)).toBeVisible({ timeout: 15_000 });

  // B: 候補一覧から04を開きYES→両者成立(最後のYESはallIn状態→チャットを開く→05)
  await b.page.goto("/#/proposals");
  await b.page.locator("#proposalsScreen a.sb-row-link").first().click();
  await expect(b.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await b.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(b.page.getByText("全員が参加しました。")).toBeVisible({ timeout: 15_000 });
  await b.page.getByRole("button", { name: "チャットを開く" }).click();
  await expect(b.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  // A: 再訪で05成立姿(チャット)・成立通知
  await a.page.goto("/#/standby");
  await a.page.goto(`/#/latches/${latch.id}`);
  await expect(a.page.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });
  const marker = `p01-${Date.now().toString(36)}`;
  await a.page.getByPlaceholder("メッセージを送る").fill(marker);
  await a.page.getByRole("button", { name: "送信" }).click();
  await expect(a.page.getByText(marker)).toBeVisible();
  // B側は30秒ポーリングのため再訪(リロード)で即時反映を確認する(H3と同手法)
  await b.page.goto(`/#/latches/${latch.id}`);
  await b.page.reload();
  await expect(b.page.getByText(marker)).toBeVisible({ timeout: 30_000 });

  await b.close();
  await a.close();
});

test("成立までの通知がお知らせに届く(proposal+成立の2種)", async ({ openAuthed }) => {
  const userA = await provisionUser({ tag: "p01n" });
  const userB = await provisionUser({ tag: "p01n" });
  const timeText = futureTimeText(6); // +6hスロット=フルチェーンテスト(+2h)と交差しない
  const rawText = `天文館で ${timeText} から 2人でランチ`;
  const a = await openAuthed(userA, "/#/");
  await depositViaUi(a.page, { text: rawText, timeText });
  const b = await openAuthed(userB, "/#/");
  await depositViaUi(b.page, { text: rawText, timeText });
  const latch = await waitForLatch(userA);

  // A YES→B YESで成立→Aにお知らせ2種(proposal+latch_matched)が届く
  const yesA = await api("POST", `/v1/latches/${latch.id}/response`, {
    token: userA.access,
    body: { response: "yes" },
  });
  expect(yesA.status).toBe(200);
  await b.page.goto("/#/proposals");
  await b.page.locator("#proposalsScreen a.sb-row-link").first().click();
  await b.page.locator('[data-role="respond"][data-value="yes"]').click();
  await expect(b.page.getByText("全員が参加しました。")).toBeVisible({ timeout: 15_000 });

  await a.page.goto("/#/");
  // findings#2修正済み: 初回オープンが必ずGETし直すため、テスト中に届いた
  // 通知(proposal+成立の2種)も開いた時点の最新として描画される
  // (旧挙動は起動時preloadのスナップショットを使い開き直しが必要だった)。
  await a.page.getByRole("button", { name: "お知らせ" }).click();
  await expect(a.page.getByText(/条件が合う候補があります/).first()).toBeVisible({ timeout: 15_000 });
  await expect(a.page.getByText("LATCHが成立しました").first()).toBeVisible({ timeout: 15_000 });
  await b.close();
  await a.close();
});
