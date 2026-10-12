// U-07(backlog §5): visibility 2値比較+否定的assert(受け入れ#22/#23)。
// 同一候補(同一Intent対)をsummary_only(全フィールド)/hidden_until_match(最小形)
// の2つのlatchで配置し(=02の「API直叩きで設定」をfixtureのproposal形状指定が
// 担当・UIは常にhidden_until_match確定送信)、04表示の差分(サマリ行の有無)と、
// raw_text・表示名・NG条件・座標が画面に出ないことを実ブラウザで証明する。
// API面の2値分岐はstaging S2#22/G3で実施済みのため、ここは表示面が本体。
import { test, expect } from "./helpers.js";
import {
  cleanupAll,
  provisionUser,
  createIntent,
  insertPairLatch,
  sql,
} from "./helpers.js";

const RAW_MARKER = "u07生文マーカー海鮮避け"; // 相手Intentのraw_text固有値
const NG_TEXT = "海鮮が苦手"; // 相手IntentのNG条件(soft_constraints・D-04)

test.afterAll(cleanupAll);

// 相手(userB)由来の非開示4点(raw_text・表示名・NG条件・座標)が画面に出ない。
// 座標はgeo_centerの実値(psql::text)とその丸め表記(fixed 4/6桁)も検査する
// — 受け入れ#23「座標精度の値が表示されない」。
async function assertNothingDisclosed(page, name, intentId) {
  const body = page.locator("body");
  await expect(body.getByText(RAW_MARKER)).toHaveCount(0);
  await expect(body.getByText(name)).toHaveCount(0);
  await expect(body.getByText(NG_TEXT)).toHaveCount(0);
  const lon = sql(`SELECT ST_X(geo_center::geometry)::text FROM intents WHERE id='${intentId}'::uuid;`);
  const lat = sql(`SELECT ST_Y(geo_center::geometry)::text FROM intents WHERE id='${intentId}'::uuid;`);
  expect(lon).toBeTruthy();
  expect(lat).toBeTruthy();
  for (const value of [lon, lat, Number(lon).toFixed(4), Number(lon).toFixed(6), Number(lat).toFixed(4), Number(lat).toFixed(6)]) {
    await expect(body.getByText(value)).toHaveCount(0);
  }
}

test("同一条件の候補を2値で配置: summary_onlyはサマリ表示・hiddenは最小形+非開示4点(受け入れ#22/#23)", async ({ openAuthed }) => {
  const userA = await provisionUser({ tag: "u07a" });
  const userB = await provisionUser({ tag: "u07b" });
  const userC = await provisionUser({ tag: "u07c" });
  const intentA = await createIntent(userA);
  // B/Cは同一条件(天文館・+2h・meal・2人)で、非開示assert用の固有値だけ載せる。
  // 同一Intent対へのopen状態latchの二重挿入はuq_latches_intent_ids_open
  // (M2 0004)で禁止されるため、2値は別ペア(A+B/A+C)で配置する
  const intentB = await createIntent(userB, { rawText: RAW_MARKER, softConstraints: [NG_TEXT] });
  const intentC = await createIntent(userC, { rawText: RAW_MARKER, softConstraints: [NG_TEXT] });

  // 同一条件の候補を2つのlatchへ — 違いはproposal形状(visibility)だけ
  const latchFull = insertPairLatch(intentA, intentB, { status: "proposed" }); // 既定=summary_only全形
  const latchMinimal = insertPairLatch(intentA, intentC, {
    status: "proposed",
    proposal: { headcount: 2, match_level: "high" }, // hidden_until_match最小形
  });

  // -- summary_only(全形)の04: 4情報カード+照合表が出る --------------------
  const full = await openAuthed(userA, `/#/latches/${latchFull}`);
  await expect(full.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(full.page.locator(".pv-cards")).toBeVisible();
  await expect(full.page.locator('[data-role="pv-fit"]')).toBeVisible();
  await expect(full.page.getByText("あなたの条件との照合")).toBeVisible();
  await expect(full.page.locator(".pv-card-value", { hasText: "天文館" })).toBeVisible(); // エリア(サマリ行)
  // 非開示4点(全形は最も多く出す形のため、ここで出なければ他方でも出ない)
  await assertNothingDisclosed(full.page, "E2E u07b", intentB);

  // -- hidden_until_match(最小形)の04: サマリなしが本体 ----------------------
  const minimal = await openAuthed(userA, `/#/latches/${latchMinimal}`);
  await expect(minimal.page.getByText("条件が、揃いそうです。")).toBeVisible({ timeout: 15_000 });
  await expect(minimal.page.locator(".pv-cards")).toHaveCount(0);
  await expect(minimal.page.locator('[data-role="pv-fit"]')).toHaveCount(0);
  await expect(minimal.page.getByText("条件が合う候補があります").first()).toBeVisible();
  await expect(
    minimal.page.getByText("くわしい内容は、全員が「参加する」と答えたときにわかります"),
  ).toBeVisible();
  // 回答期限と必要人数は最小形でも出る(受け入れ#22の「一致度・回答期限のみ」)
  await expect(minimal.page.getByText("回答期限まで")).toBeVisible();
  await expect(minimal.page.locator('[data-role="who"]')).toHaveText("あなた ＋ まだ見えない1人");
  await expect(minimal.page.locator('[data-role="respond"][data-value="yes"]')).toBeEnabled();
  // 非開示4点(最小形)
  await assertNothingDisclosed(minimal.page, "E2E u07c", intentC);

  // -- 一覧(#/proposals)のサマリ行の差分(自分のlatchへhrefでスコープ) --------
  await minimal.page.goto("/#/proposals");
  const fullRow = minimal.page.locator(`a.sb-row-link[href="#/latches/${latchFull}"]`);
  await expect(fullRow).toBeVisible({ timeout: 15_000 });
  await expect(fullRow).toContainText("天文館"); // summary_only=条件サマリ(時間・エリア)
  const minimalRow = minimal.page.locator(`a.sb-row-link[href="#/latches/${latchMinimal}"]`);
  await expect(minimalRow).toContainText("条件が合う候補があります"); // hidden=サマリなし
  await expect(minimalRow).not.toContainText("天文館");

  await full.close();
  await minimal.close();
});
