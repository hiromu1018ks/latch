// E2E共通helper。既定の対象はci常設環境(実API=127.0.0.1:8000・実DB=latch-ci-db-1)。
// LATCH_E2E_TARGET=staging でstaging(nginx 18000・DB=latch-staging-db-1・.staging/keys)
// を向く(iss/aud/kidはSettings既定で共通・鍵だけ相違)。cloudflare(cf-dev 8787)は
// 実APIのみ使いDB直接操作はしない(D1はAPI経由で観測)。
// ユーザー払い出しは契約試験(tests/contracts/reference_harness.py)と同じ正規経路:
// IdPトークン(RS256)→POST /v1/auth/token→POST /v1/users→consent。
// latch行は同harnessと同じ直接INSERT(IntentのみAPI作成・パイプライン非依存)。
import { execFileSync } from "node:child_process";
import { createSign } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { test as base, expect } from "@playwright/test";

export const TARGET = process.env.LATCH_E2E_TARGET ?? "ci";
export const API_BASE =
  process.env.LATCH_E2E_API ?? (TARGET === "staging" ? "http://127.0.0.1:18000" : TARGET === "cloudflare" ? "http://127.0.0.1:8787" : "http://127.0.0.1:8000");
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const IDP_KEY = readFileSync(
  TARGET === "staging"
    ? path.join(REPO_ROOT, ".staging/keys/idp_test_private.pem")
    : path.join(REPO_ROOT, "backend/src/latch/auth/testkeys/idp_test_private.pem"),
);
const DB_CONTAINER = TARGET === "staging" ? "latch-staging-db-1" : "latch-ci-db-1";
const IDP_KID = "test-idp-1";
const IDP_ISS = "https://idp.ci.latch.test/google";
const IDP_AUD = "latch-test-app";

let seq = 0;
const createdLatches = [];
const createdUsers = [];

// stub Parserは入力にかかわらず固定応答(場所=東京駅・過去時刻)のため、UIの主幹
// フローは02の行編集で場所と時刻を書き換えてから保存する(実ユーザーの修正操作)。
export function futureTimeText(hoursAhead = 1) {
  const ms = Date.now() + hoursAhead * 3_600_000;
  const jst = new Date(ms + 9 * 3_600_000);
  const mm = jst.getUTCMinutes();
  const add = mm === 0 ? 0 : mm <= 30 ? 30 - mm : 60 - mm;
  const t = new Date(ms + add * 60_000 + 9 * 3_600_000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${t.getUTCMonth() + 1}/${t.getUTCDate()} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
}

export function issueIdpToken(subject) {
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(
    JSON.stringify({ alg: "RS256", typ: "JWT", kid: IDP_KID }),
  ).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({ iss: IDP_ISS, aud: IDP_AUD, sub: subject, iat: now, exp: now + 3600 }),
  ).toString("base64url");
  const sig = createSign("RSA-SHA256").update(`${header}.${payload}`).sign(IDP_KEY);
  return `${header}.${payload}.${sig.toString("base64url")}`;
}

export async function api(method, pathName, { token, body } = {}) {
  const res = await fetch(`${API_BASE}${pathName}`, {
    method,
    headers: {
      ...(body != null ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  return { status: res.status, data };
}

export async function provisionUser({ consent = true, register = true, tag = "e2e", birthDate = "1990-06-15" } = {}) {
  const subject = `${tag}-${Date.now().toString(36)}-${(seq++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const exchanged = await api("POST", "/v1/auth/token", {
    body: { provider: "google", idp_token: issueIdpToken(subject) },
  });
  if (exchanged.status !== 200) {
    throw new Error(`auth/token failed: ${exchanged.status} ${JSON.stringify(exchanged.data)}`);
  }
  const user = {
    subject,
    idpToken: null,
    access: exchanged.data.access_token,
    refresh: exchanged.data.refresh_token,
  };
  user.idpToken = issueIdpToken(subject); // パネル貼付用に新規発行(1時間有効)
  if (register) {
    const created = await api("POST", "/v1/users", {
      token: user.access,
      body: { display_name: `E2E ${tag}`, birth_date: birthDate },
    });
    if (created.status !== 201) {
      throw new Error(`users failed: ${created.status} ${JSON.stringify(created.data)}`);
    }
    user.userId = created.data.user.id;
    if (consent) {
      const agreed = await api("POST", "/v1/users/me/consent", { token: user.access });
      if (agreed.status !== 200) {
        throw new Error(`consent failed: ${agreed.status} ${JSON.stringify(agreed.data)}`);
      }
    }
  }
  createdUsers.push(user);
  return user;
}

// rawText/softConstraintsはU-07用(相手Intentの非開示assertに使う固有値の注入)。
export async function createIntent(
  user,
  { status = "active", location = "天文館", hoursAhead = 2, rawText, softConstraints } = {},
) {
  const start = new Date(Date.now() + hoursAhead * 3_600_000).toISOString();
  const res = await api("POST", "/v1/intents", {
    token: user.access,
    body: {
      raw_text: rawText ?? `e2e ${location} ${start}`,
      status,
      structured_intent: {
        category: { primary: "meal", secondary: null },
        alcohol_involved: false,
        time: { start, end: null },
        location: { name: location },
        ...(softConstraints ? { soft_constraints: softConstraints } : {}),
      },
    },
  });
  if (res.status !== 201) {
    throw new Error(`intents failed: ${res.status} ${JSON.stringify(res.data)}`);
  }
  return res.data.intent.id;
}

export function sql(statement) {
  return execFileSync(
    "docker",
    ["exec", DB_CONTAINER, "psql", "-U", "latch", "-d", "latch", "-tA", "-c", statement],
    { encoding: "utf8" },
  ).trim();
}

export function insertPairLatch(intentA, intentB, { status = "proposed", proposal } = {}) {
  // 既定proposalは実パイプラインbuild_proposal()のsummary_only全フィールド形を
  // 模す(headcount+match_levelのみだとUIは最小形=照合表なしで描画されるため)。
  // proposalを渡すと最小形(hidden_until_match実運用姿)等を指定できる。
  const t = new Date(Date.now() + 2 * 3_600_000 + 9 * 3_600_000);
  const pad = (n) => String(n).padStart(2, "0");
  const timeSummary = `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
  const full = {
    time_summary: timeSummary,
    area_name: "天文館",
    headcount: 2,
    category_primary: "meal",
    category_secondary: null,
    budget: null,
    match_level: "medium",
  };
  const json = JSON.stringify(proposal ?? full);
  const out = sql([
    "INSERT INTO latches (intent_ids, group_candidate_id, proposal, score, status, response_deadline, expires_at, created_at)",
    `VALUES (ARRAY['${intentA}'::uuid,'${intentB}'::uuid], NULL,`,
    `  '${json}'::jsonb, 0.85, '${status}',`,
    "  now() + interval '3 hours', now() + interval '120 hours', now())",
    "RETURNING id;",
  ].join(" "));
  // psqlはRETURNING行に加えてコマンドタグ(INSERT 0 1)もstdoutへ出すことがあるためuuid行のみ取り出す
  const id = out
    .split("\n")
    .map((line) => line.trim())
    .find((line) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(line));
  if (!id) throw new Error(`latch id not parsed from psql output: ${out}`);
  createdLatches.push(id);
  return id;
}

export function expireResponseDeadline(latchId) {
  sql(`UPDATE latches SET response_deadline = now() - interval '1 minute' WHERE id = '${latchId}'::uuid;`);
}

// completed fixture(U-04 attendance用)。実運用ではsweeperが対象時刻経過で
// status=completed+completed_atを書き、回答確定時にcalibration_records行が
// 作られるため、その2点を直接投入する(attendanceの書込先はcalibration_records・
// 窓判定はcompleted_at+3日=service.is_attendance_window_open)。actual_attendedは
// NULLのまま申告待ちにする。
export function insertCompletedLatch(intentA, intentB, { proposal } = {}) {
  const latchId = insertPairLatch(intentA, intentB, { status: "completed", proposal });
  sql(`UPDATE latches SET completed_at = now() WHERE id = '${latchId}'::uuid;`);
  sql(
    [
      "INSERT INTO calibration_records (latch_id, intent_ids, prediction, proposal_snapshot, actual_responses, matched, created_at, updated_at)",
      "SELECT id, intent_ids, '{}'::jsonb, proposal, '[]'::jsonb, true, now(), now()",
      `FROM latches WHERE id = '${latchId}'::uuid;`,
    ].join(" "),
  );
  return latchId;
}

// グループ成立fixture(3人)。group_candidates行+group_candidate_idつきlatches行を
// 直接INSERTする(契約harnessの直接INSERT手法と同じ・パイプライン非依存)。
// member_scoresは回答時のcalibration用のためGET詳細では未使用 — 最小形で投入する。
export function insertGroupLatch(intentIds, { status = "matched" } = {}) {
  const ids = intentIds.map((id) => `'${id}'::uuid`).join(",");
  const uuidOf = (out) =>
    out
      .split("\n")
      .map((line) => line.trim())
      .find((line) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(line));
  const gidOut = sql(
    [
      "INSERT INTO group_candidates (intent_ids, member_scores, aggregate_score, status, created_at, updated_at)",
      `VALUES (ARRAY[${ids}], '{}'::jsonb, 0.85, 'confirmed', now(), now())`,
      "RETURNING id;",
    ].join(" "),
  );
  const gid = uuidOf(gidOut);
  if (!gid) throw new Error(`group_candidate id not parsed: ${gidOut}`);
  const proposal = JSON.stringify({ headcount: intentIds.length, match_level: "high" });
  const out = sql([
    "INSERT INTO latches (intent_ids, group_candidate_id, proposal, score, status, response_deadline, expires_at, created_at)",
    `VALUES (ARRAY[${ids}], '${gid}'::uuid,`,
    `  '${proposal}'::jsonb, 0.85, '${status}',`,
    "  now() + interval '3 hours', now() + interval '120 hours', now())",
    "RETURNING id;",
  ].join(" "));
  const id = uuidOf(out);
  if (!id) throw new Error(`latch id not parsed from psql output: ${out}`);
  createdLatches.push(id);
  return { latchId: id, groupCandidateId: gid };
}

// 通報の受け付け実績をDB側から数える(POST /v1/reportsは受付のみで取得APIなし)。
export function reportsCount({ reporterId, reporteeId, latchId } = {}) {
  const conds = [];
  if (reporterId) conds.push(`reporter_id='${reporterId}'::uuid`);
  if (reporteeId) conds.push(`reportee_id='${reporteeId}'::uuid`);
  if (latchId) conds.push(`latch_id='${latchId}'::uuid`);
  return Number(sql(`SELECT count(*) FROM reports ${conds.length ? "WHERE " + conds.join(" AND ") : ""};`));
}

export async function pairLatchFixture({ status = "proposed", tag = "e2e", proposal } = {}) {
  const userA = await provisionUser({ tag });
  const userB = await provisionUser({ tag });
  const intentA = await createIntent(userA);
  const intentB = await createIntent(userB);
  const latchId = insertPairLatch(intentA, intentB, { status, proposal });
  return { userA, userB, intentA, intentB, latchId };
}

// ブラウザ側sessionStorage/localStorageへトークン直置き(session.jsの保存形式)。
export async function seedStorage(context, { access, refresh }) {
  await context.addInitScript(
    ([a, r]) => {
      if (a != null) sessionStorage.setItem("latch-access", a);
      if (r != null) localStorage.setItem("latch-refresh", r);
    },
    [access ?? null, refresh ?? null],
  );
}

// afterAllから呼ぶベストエフォート後片付け(latch系行→ユーザー退会カスケード)。
export async function cleanupAll() {
  if (TARGET !== "cloudflare") {
    // 参照実装系(ci/staging)はlatch子テーブルを直接SQL削除できる。
    // cloudflare(cf-dev)はD1を直接触らない(APIの退会カスケードに任せる)
    for (const id of createdLatches.splice(0)) {
      try {
        // FK順序: latch子tables → latches → group_candidates(latchesから参照されるため後)
        const gid = sql(`SELECT group_candidate_id FROM latches WHERE id='${id}'::uuid;`);
        sql(
          `DELETE FROM messages WHERE latch_id='${id}'::uuid; DELETE FROM latch_status_events WHERE latch_id='${id}'::uuid; DELETE FROM calibration_records WHERE latch_id='${id}'::uuid; DELETE FROM notifications WHERE payload->>'latch_id'='${id}'; DELETE FROM reports WHERE latch_id='${id}'::uuid; DELETE FROM latches WHERE id='${id}'::uuid;`,
        );
        if (/^[0-9a-f-]{36}$/.test(gid)) {
          sql(`DELETE FROM group_candidates WHERE id='${gid}'::uuid;`);
        }
      } catch {
        /* 残ってもci DBの既存運用(リセットなし)と同じため警告のみ */
      }
    }
  }
  for (const user of createdUsers.splice(0)) {
    try {
      const ex = await api("POST", "/v1/auth/token", {
        body: { provider: "google", idp_token: issueIdpToken(user.subject) },
      });
      const token = ex.status === 200 ? ex.data.access_token : user.access;
      await api("DELETE", "/v1/users/me", { token });
    } catch {
      /* best-effort */
    }
  }
}

// 認証済み単一ユーザーを前提にするspec用の拡張fixture(1テスト=1ユーザー)。
// openAuthedは第二ユーザー等の手動コンテキスト用: 設定のvideo:"on"はフィクスチャ
// 経由のコンテキストにしか自動適用されないため、ここで明示的に録画し
// testInfo.attachでテスト結果へ紐付ける(HTMLレポート・ギャラリー両方で見える)。
export const test = base.extend({
  user: async ({}, use) => {
    const user = await provisionUser({});
    await use(user);
  },
  page: async ({ page, user }, use) => {
    await seedStorage(page.context(), user);
    await use(page);
  },
  openAuthed: [
    async ({ browser }, use, testInfo) => {
      const opener = async (user, url) => {
        const context = await browser.newContext({
          viewport: { width: 1280, height: 720 },
          recordVideo: { dir: testInfo.outputDir, size: { width: 1280, height: 720 } },
        });
        await seedStorage(context, user);
        const page = await context.newPage();
        await page.goto(url);
        return {
          context,
          page,
          close: async () => {
            const video = page.video();
            await context.close();
            if (video) {
              await testInfo.attach("video", {
                path: await video.path(),
                contentType: "video/webm",
              });
            }
          },
        };
      };
      await use(opener);
    },
    { auto: true },
  ],
});
export { expect };
