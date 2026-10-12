// 期限切れの実描画(U-19+T-01+P-08の統合シナリオ + T-02・backlog §4/§5・受け入れ#16/#4後半)。
//
// 仕組み上の要点(matrix §7.3へも記録):
// - 04の期限切れ表示(タイマー0:00・回答2択disabled)はブラウザ実時間の残時間計算
//   のため、staging Clockを進めても描画されない → SQLでresponse_deadlineを
//   過去化するのが正規手段(backlog U-19記載どおり。Clock不使用=他specと干渉なし)
// - 期限切れlatchのstatus=expired化・intentのstatus=expired化は実sweeper
//   (60秒tick)の成果として観測する(P-08「expiry sweeperの成果」)
// - SQLは対象IDに限定したUPDATEのみ(全行過去化は他テストを壊すため使わない)
// - スロット分離(A-01規律・3時間マッチング窓): U-19=+11.5h・T-02=+12.5hで
//   pipeline-ui(+2h/+6h)・ground-truth(D(2..5)の+39h以降)と窓外。失敗時にIntent
//   を活性のまま残さないよう各テストはfinallyで自分のIntentをDELETEする
//   (残存Intentは次run・並走specと交差マッチしてD-08上限を食う)
import {
  test,
  expect,
  provisionUser,
  createIntent,
  insertPairLatch,
  sql,
  api,
  cleanupAll,
} from "../e2e/helpers.js";
import { ensureStagingClockReal } from "./helpers.js";

test.beforeAll(() => {
  ensureStagingClockReal();
});

test.afterAll(cleanupAll);

test.setTimeout(300_000);

async function cancelIntents(users, intentIds) {
  for (const iid of intentIds) {
    for (const u of users) {
      try {
        await api("DELETE", `/v1/intents/${iid}`, { token: u.access });
      } catch {
        // 後始末のベストエフォート(所有者側だけ成功すればよい)
      }
    }
  }
}

async function waitStatus(table, id, status, { timeoutMs = 180_000 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const got = sql(`SELECT status FROM ${table} WHERE id='${id}'::uuid;`);
    if (got === status) return;
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  throw new Error(`waitStatus timeout: ${table} ${id} が ${status} にならない`);
}

test("回答期限経過→04タイマー0:00→sweeper expired→04 closed・03/お知らせから静かに消える(U-19+T-01+P-08)", async ({ openAuthed }) => {
  const userA = await provisionUser({ tag: "exp19a" });
  const userB = await provisionUser({ tag: "exp19b" });
  const intentA = await createIntent(userA, { hoursAhead: 11.5 });
  const intentB = await createIntent(userB, { hoursAhead: 11.5 });
  const latchId = insertPairLatch(intentA, intentB, {});
  try {
    // お知らせ行(実パイプラインのapply_proposalが作るのと同形のproposal通知)
    sql(
      `INSERT INTO notifications (id, user_id, type, payload, created_at)`
      + ` VALUES ('${crypto.randomUUID()}'::uuid, '${userA.userId}'::uuid, 'proposal',`
      + ` '{"latch_id":"${latchId}"}'::jsonb, now());`,
    );

    // 提案姿→期限過去化→タイマー0:00+回答2択disabled(T-01前半・DeadlineTimer)
    const mine = await openAuthed(userA, `/#/latches/${latchId}`);
    await expect(mine.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
    sql(
      `UPDATE latches SET response_deadline = now() - interval '1 minute' WHERE id = '${latchId}'::uuid;`,
    );
    await mine.page.reload();
    await expect(mine.page.locator('[data-role="deadline"]')).toHaveText("0:00", { timeout: 15_000 });
    await expect(mine.page.locator('[data-role="respond"][data-value="yes"]')).toBeDisabled();
    await expect(mine.page.locator('[data-role="respond"][data-value="defer"]')).toBeDisabled();

    // 実sweeper(60秒tick)がproposed→expiredへ(期限切れ回答で成立しない)
    await waitStatus("latches", latchId, "expired");

    // 04 closed姿(U-19/P-08):「この提案は成立しませんでした」+ホーム導線
    // (お知らせpopoverの行にも同文言が出るため#detailScreenへスコープ)
    await mine.page.reload();
    await expect(
      mine.page.locator("#detailScreen").getByText("この提案は成立しませんでした"),
    ).toBeVisible({ timeout: 15_000 });

    // 03候補一覧から静かに消える(T-01後半: home.splitStatusesはexpiredを含まない)。
    // 行は自分のlatchだけをhrefで特定する(stub Jevは常に0.9を返すため、時間飽和
    // (Δ≥3hでrule寄与0)でもsimilarity次第で他テストのIntentと交差latchが
    // 生じ得る=テスト環境ノイズ。matrix §7.3参照)
    await mine.page.goto("/#/proposals");
    await expect(
      mine.page.locator(`#proposalsScreen a.sb-row-link[href="#/latches/${latchId}"]`),
    ).toHaveCount(0);

    // お知らせ行は終了状態の統一文言へ(view.notificationLines・CLOSED_TEXT)
    await mine.page.goto("/#/");
    await mine.page.getByRole("button", { name: "お知らせ" }).click();
    await expect(
      mine.page
        .locator(`#noticeList [href="#/latches/${latchId}"]`)
        .getByText("この提案は成立しませんでした"),
    ).toBeVisible({ timeout: 15_000 });
    await mine.close();
  } finally {
    await cancelIntents([userA, userB], [intentA, intentB]);
  }
});

test("Intent expires_at経過→sweeperがexpired化→03待機中から消失(T-02・受け入れ#4後半)", async ({ openAuthed }) => {
  const userC = await provisionUser({ tag: "expt02" });
  const intentC = await createIntent(userC, { hoursAhead: 12.5 });
  try {
    const mine = await openAuthed(userC, "/#/standby");
    await expect(mine.page.locator(".sb-tag", { hasText: "天文館" })).toBeVisible({ timeout: 15_000 });

    // expires_at過去化→実sweeperの_expire_intent(draft/paused/active→expired)
    sql(
      `UPDATE intents SET expires_at = now() - interval '1 minute' WHERE id = '${intentC}'::uuid;`,
    );
    await waitStatus("intents", intentC, "expired");

    // 03の待機中から消える(GET /v1/intents?status=active に含まれなくなる)
    await mine.page.reload();
    await expect(mine.page.locator(".sb-tag", { hasText: "天文館" })).toHaveCount(0, { timeout: 15_000 });
    await mine.close();
  } finally {
    await cancelIntents([userC], [intentC]);
  }
});
