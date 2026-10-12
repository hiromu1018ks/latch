// A-01(backlog §3・P0): マッチング精度のグラウンドトゥルース検証。
// 適合対(正例)には提案が届き・不適合対(負例)には一度も提案が来ないことを
// 同一run内で両方向assertする(staging S1は個別項目単位で統括assertが不存在だった)。
// IntentはAPIで正確な構造化値を与え(境界の時間差・距離・人数・予算はUI編集で
// 制御できないため)・判定結果はUI(候補一覧・04画面)でも観測する。
//
// fixtureスロット(すべてJST・相互に3時間以上離す=Layer 1時間窓で交差しない):
//   適合   summary_only×2     … 全フィールド提案(照合表つき04)
//   適合   summary×hidden混在 … 最小形提案(visibilityはLayer 1の判定対象外)
//   不適合 時間(+5hで窓外) / 距離(天文館↔祇園之洲町=2.4km>半径和2km)
//         / 人数(相手min3) / 予算(ペアmin300<500) / カテゴリ不一致
import { test, expect, provisionUser, api, cleanupAll } from "../e2e/helpers.js";
import {
  ensureStagingClockReal,
  createGroundTruthIntent,
  deleteUsers,
} from "./helpers.js";

test.setTimeout(360_000);
test.afterAll(cleanupAll);

const D = (days, hhmm) => {
  // JST固定の日時文字列(D+n日の指定時刻)。基準は実行日のJST翌日以降のみ使う
  const base = new Date(Date.now() + 9 * 3_600_000); // JST現在
  const d = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth(), base.getUTCDate() + days, 0, 0) - 9 * 3_600_000);
  const [h, m] = hhmm.split(":").map(Number);
  return new Date(d.getTime() + h * 3_600_000 + m * 60_000).toISOString();
};

test("グラウンドトゥルース: 適合対のみ提案・不適合対には来ない(両方向assert)", async ({ openAuthed }) => {
  ensureStagingClockReal();
  const created = []; // finally退会用
  const pair = async (tag, optsA, optsB) => {
    const a = await provisionUser({ tag });
    const b = await provisionUser({ tag });
    created.push(a, b);
    const ia = await createGroundTruthIntent(a, optsA);
    const ib = await createGroundTruthIntent(b, optsB);
    return { a, b, ia, ib };
  };
  const EXPIRES = D(7, "05:00"); // 全fixture共通(D+7 5:00・sweeperに存活させる)

  // 不適合対はwhenだけでは足りない差分をoptsBへ
  const fitSummary = await pair("gt-fs", { when: D(2, "06:30"), expiresAt: EXPIRES }, { when: D(2, "06:30"), expiresAt: EXPIRES });
  const fitMixed = await pair(
    "gt-fm",
    { when: D(3, "06:30"), expiresAt: EXPIRES },
    { when: D(3, "06:30"), expiresAt: EXPIRES, visibility: "hidden_until_match" },
  );
  const unfitTime = await pair(
    "gt-xt",
    { when: D(4, "06:30"), expiresAt: EXPIRES },
    { when: D(4, "06:30"), peerWhen: D(4, "11:30"), expiresAt: EXPIRES },
  );
  const unfitDist = await pair(
    "gt-xd",
    { when: D(5, "06:30"), expiresAt: EXPIRES },
    { when: D(5, "06:30"), location: "祇園之洲町", expiresAt: EXPIRES },
  );
  const unfitHeadc = await pair(
    "gt-xh",
    { when: D(2, "20:30"), expiresAt: EXPIRES },
    { when: D(2, "20:30"), participants: [3, 4], expiresAt: EXPIRES },
  );
  const unfitBudget = await pair(
    "gt-xb",
    { when: D(3, "20:30"), budgetMax: 300, expiresAt: EXPIRES },
    { when: D(3, "20:30"), budgetMax: 400, expiresAt: EXPIRES },
  );
  const unfitCat = await pair(
    "gt-xc",
    { when: D(4, "20:30"), category: "meal", secondary: null, expiresAt: EXPIRES },
    { when: D(4, "20:30"), category: "activity", expiresAt: EXPIRES },
  );

  const myLatches = async (user) => {
    const r = await api("GET", "/v1/latches", { token: user.access });
    return r.data?.items ?? [];
  };

  try {
    // (1) 正例: 両適合対に提案が届く(debounce+tick込み・余裕180s)
    const waitProposed = async (user, label) => {
      const started = Date.now();
      for (;;) {
        const items = await myLatches(user);
        const hit = items.find((i) => i.status === "proposed");
        if (hit) return hit;
        if (Date.now() - started > 180_000) throw new Error(`正例${label}に提案が来ない`);
        await new Promise((r) => setTimeout(r, 5000));
      }
    };
    const full = await waitProposed(fitSummary.a, "summary_only×2");
    const minimal = await waitProposed(fitMixed.a, "summary×hidden混在");
    // proposal形状: summary_only×2=全フィールド / 混在=最小形(visibilityは絞り込まない)
    expect(Object.keys(full.proposal)).toContain("time_summary");
    expect(full.proposal).toEqual(
      expect.objectContaining({ time_summary: expect.any(String), area_name: expect.any(String), headcount: 2 }),
    );
    expect(minimal.proposal).toEqual({ headcount: 2, match_level: expect.any(String) });

    // (2) 負例: 最終Intent作成から90s経過(正例観測+grace)後もlatchが0件のまま
    await new Promise((r) => setTimeout(r, 30_000));
    const unfitness = [
      ["時間+5h", unfitTime],
      ["距離2.4km", unfitDist],
      ["人数min3", unfitHeadc],
      ["予算ペア300", unfitBudget],
      ["カテゴリ不一致", unfitCat],
    ];
    for (const [label, p] of unfitness) {
      for (const side of [p.a, p.b]) {
        const items = await myLatches(side);
        expect(items, `不適合(${label})にlatchが発生してはならない ${side.subject}`).toEqual([]);
      }
    }

    // (3) UI側の行列観測: 正例=候補一覧に行+04(全形/最小形)・負例=空状態
    const pos = await openAuthed(fitSummary.a, "/#/proposals");
    await expect(pos.page.locator("#proposalsScreen a.sb-row-link").first()).toBeVisible({ timeout: 15_000 });
    await pos.page.locator("#proposalsScreen a.sb-row-link").first().click();
    await expect(pos.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
    await expect(pos.page.locator('[data-role="pv-fit"]')).toBeVisible(); // 全形=照合表あり
    await pos.close();

    const mix = await openAuthed(fitMixed.a, "/#/proposals");
    await mix.page.locator("#proposalsScreen a.sb-row-link").first().click();
    await expect(mix.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
    await expect(mix.page.locator('[data-role="pv-fit"]')).toHaveCount(0); // 最小形=照合表なし
    await mix.close();

    const neg = await openAuthed(unfitBudget.a, "/#/proposals");
    await expect(neg.page.getByText("まだ提案はありません")).toBeVisible({ timeout: 15_000 });
    await expect(neg.page.getByText("条件が重なると、ここに届きます。")).toBeVisible();
    await neg.close();
  } finally {
    await deleteUsers(created);
  }
});
