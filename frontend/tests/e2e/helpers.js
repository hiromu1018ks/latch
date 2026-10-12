// E2E共通helper。実API=compose常設(127.0.0.1:8000)・実DB=latch-ci-db-1が前提。
// ユーザー払い出しは契約試験(tests/contracts/reference_harness.py)と同じ正規経路:
// IdPトークン(RS256・backend同梱テスト鍵)→POST /v1/auth/token→POST /v1/users→consent。
// latch行は同harnessと同じ直接INSERT(IntentのみAPI作成・パイプライン非依存)。
import { execFileSync } from "node:child_process";
import { createSign } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { test as base, expect } from "@playwright/test";

export const API_BASE = process.env.LATCH_E2E_API ?? "http://127.0.0.1:8000";
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const IDP_KEY = readFileSync(
  path.join(REPO_ROOT, "backend/src/latch/auth/testkeys/idp_test_private.pem"),
);
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

export async function provisionUser({ consent = true, register = true, tag = "e2e" } = {}) {
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
      body: { display_name: `E2E ${tag}`, birth_date: "1990-06-15" },
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

export async function createIntent(user, { status = "active", location = "天文館", hoursAhead = 2 } = {}) {
  const start = new Date(Date.now() + hoursAhead * 3_600_000).toISOString();
  const res = await api("POST", "/v1/intents", {
    token: user.access,
    body: {
      raw_text: `e2e ${location} ${start}`,
      status,
      structured_intent: {
        category: { primary: "meal", secondary: null },
        alcohol_involved: false,
        time: { start, end: null },
        location: { name: location },
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
    ["exec", "latch-ci-db-1", "psql", "-U", "latch", "-d", "latch", "-tA", "-c", statement],
    { encoding: "utf8" },
  ).trim();
}

export function insertPairLatch(intentA, intentB, { status = "proposed" } = {}) {
  // proposalは実パイプラインbuild_proposal()のsummary_only全フィールド形を模す
  // (headcount+match_levelのみだとUIは最小形=照合表なしで描画されるため)。
  const t = new Date(Date.now() + 2 * 3_600_000 + 9 * 3_600_000);
  const pad = (n) => String(n).padStart(2, "0");
  const timeSummary = `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
  const proposal = {
    time_summary: timeSummary,
    area_name: "天文館",
    headcount: 2,
    category_primary: "meal",
    category_secondary: null,
    budget: null,
    match_level: "medium",
  };
  const out = sql([
    "INSERT INTO latches (intent_ids, group_candidate_id, proposal, score, status, response_deadline, expires_at, created_at)",
    `VALUES (ARRAY['${intentA}'::uuid,'${intentB}'::uuid], NULL,`,
    `  '${JSON.stringify(proposal)}'::jsonb, 0.85, '${status}',`,
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

export async function pairLatchFixture({ status = "proposed", tag = "e2e" } = {}) {
  const userA = await provisionUser({ tag });
  const userB = await provisionUser({ tag });
  const intentA = await createIntent(userA);
  const intentB = await createIntent(userB);
  const latchId = insertPairLatch(intentA, intentB, { status });
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

export async function openAuthed(browser, user, url) {
  const context = await browser.newContext();
  await seedStorage(context, user);
  const page = await context.newPage();
  await page.goto(url);
  return { context, page };
}

// afterAllから呼ぶベストエフォート後片付け(latch系行→ユーザー退会カスケード)。
export async function cleanupAll() {
  for (const id of createdLatches.splice(0)) {
    try {
      sql(
        `DELETE FROM messages WHERE latch_id='${id}'::uuid; DELETE FROM latch_status_events WHERE latch_id='${id}'::uuid; DELETE FROM calibration_records WHERE latch_id='${id}'::uuid; DELETE FROM notifications WHERE payload->>'latch_id'='${id}'; DELETE FROM latches WHERE id='${id}'::uuid;`,
      );
    } catch {
      /* 残ってもci DBの既存運用(リセットなし)と同じため警告のみ */
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
export const test = base.extend({
  user: async ({}, use) => {
    const user = await provisionUser({});
    await use(user);
  },
  page: async ({ page, user }, use) => {
    await seedStorage(page.context(), user);
    await use(page);
  },
});
export { expect };
