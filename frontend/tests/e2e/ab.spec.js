// A-05(backlog §3・V1-P4): A/B割付の実機E2E(05 v0.8・割付はUIを持たない)。
// ①割付の発生と安定性: 実験行(running)をSQLで投入→2ユーザーのIntent作成→
// 実workerの提案評価が割付を作る(ab_assignments行)→GET /v1/users/me/abで
// 自分の割付が観測できる→再取得でも同一variant(割付の安定性)。
// ②観測APIは割付を作らない: 割付がまだ無いユーザーへのGETは空配列のまま。
// 混在ペアの対照値適用はbackend integration(test_ab_api)+cf-unit
// (test_ab_pipeline)が所有する(スコア値を固定できるのは試験環境のみのため
// E2Eでは間接証明になる・設計判断はmatrix §11)。
//
// 実験キーはnotification_daily_limit・control=treatment=6で投入する
// (割付の駆動に必要で・値が同値のため他specの提案挙動に影響しない)。
import { test, expect } from "./helpers.js";
import { api, cleanupAll, createIntent, provisionUser, sql } from "./helpers.js";

test.afterAll(cleanupAll);

const EXPERIMENT = "notification_daily_limit";

const seedExperiment = () => {
  sql(
    `INSERT INTO ab_experiments (experiment_key, status, control_value, treatment_value, started_at)
     VALUES ('${EXPERIMENT}', 'running', '6', '6', now())
     ON CONFLICT (experiment_key) DO UPDATE SET status='running',
       control_value='6', treatment_value='6', started_at=now(), stopped_at=NULL;`,
  );
};

const dropExperiment = () => {
  // FK cascadeで当該実験の割付行も掃ける(他specへの一時実験の残置を防ぐ)
  sql(`DELETE FROM ab_experiments WHERE experiment_key='${EXPERIMENT}';`);
};

const assignmentOf = (userId) =>
  sql(
    `SELECT variant FROM ab_assignments WHERE user_id='${userId}'::uuid AND experiment_key='${EXPERIMENT}';`,
  ).trim();

const waitFor = async (fn, { timeoutMs = 30_000, stepMs = 500 } = {}) => {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = fn();
    if (value) return value;
    if (Date.now() > deadline) return null;
    await new Promise((r) => setTimeout(r, stepMs));
  }
};

test("割付の発生と安定性: 提案評価を経由して割付→観測API→再取得でも同一", async ({ openAuthed }) => {
  seedExperiment();
  try {
    const user = await provisionUser({ tag: "ab" });
    await createIntent(user);

    // 実workerの提案評価(drain/try_promote)が割付を作るまで待つ
    const variant = await waitFor(() => assignmentOf(user.userId));
    expect(variant).toBeTruthy();
    expect(["control", "treatment"]).toContain(variant);

    // 観測API: 自分の割付が1行・応答形状(assigned_atつき・stratumなし)
    const mine = await api("GET", "/v1/users/me/ab", { token: user.access });
    expect(mine.status).toBe(200);
    expect(mine.data.assignments).toHaveLength(1);
    expect(mine.data.assignments[0].experiment).toBe(EXPERIMENT);
    expect(mine.data.assignments[0].variant).toBe(variant);
    expect(typeof mine.data.assignments[0].assigned_at).toBe("string");
    expect("stratum" in mine.data.assignments[0]).toBe(false);

    // 安定性: 再取得でも同一variant(永続化行が真実・再計算しない)
    const again = await api("GET", "/v1/users/me/ab", { token: user.access });
    expect(again.data.assignments[0].variant).toBe(variant);
    expect(assignmentOf(user.userId)).toBe(variant);
    await openAuthed(user, "/#/standby").then((c) => c.close()); // UI不変の往復
  } finally {
    dropExperiment();
  }
});

test("観測APIは割付を作らない: 割付前ユーザーのGETは空配列のまま", async () => {
  seedExperiment();
  try {
    const user = await provisionUser({ tag: "ab" }); // Intentを作らない=評価が走らない
    const before = await api("GET", "/v1/users/me/ab", { token: user.access });
    expect(before.status).toBe(200);
    expect(before.data.assignments).toEqual([]);
    expect(assignmentOf(user.userId)).toBe(""); // DBにも行がない
  } finally {
    dropExperiment();
  }
});
