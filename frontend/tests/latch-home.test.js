import { describe, expect, it, vi } from "vitest";
import {
  createHome,
  intentCardHtml,
  pairCandidateLatch,
  splitStatuses,
} from "../src/latch/home.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: null,
  budget: null,
  match_level: "high",
};
const latch = (status, overrides = {}) => ({
  id: LATCH_ID,
  status,
  response_deadline: "2026-12-01T12:00:00+09:00",
  expires_at: "2026-12-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal: fullProposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
  ...overrides,
});

const INTENT_ID = "aaaaaaaa-1111-4111-8111-111111111111";
// GET /v1/intents の応答要素(IntentOut・05 §5)
const intent = (overrides = {}) => ({
  id: INTENT_ID,
  status: "active",
  version: 1,
  raw_text: "今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい",
  structured_data: {},
  structured_intent: {
    category: { primary: "drinking", secondary: null },
    time: { start: "2026-10-10T20:00:00+09:00", end: null },
    location: { name: "天文館", radius_m: null },
    budget: { max: 5000, currency: "JPY" },
    participants: { min: 2, max: 4 },
  },
  expires_at: "2026-10-10T23:59:00+09:00",
  created_at: "2026-10-10T11:48:00+09:00",
  updated_at: "2026-10-10T11:48:00+09:00",
  ...overrides,
});

const NOW = "2026-10-10T12:00:00+09:00";

const mountHome = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const el = {
    intentCards: document.createElement("div"),
    standbyLine: document.createElement("p"),
    waitSection: document.createElement("section"),
  };
  el.standbyLine.innerHTML = '<span class="beacon"></span><span data-standby-count></span>';
  const onCounts = vi.fn();
  const home = createHome({ client, el, onCounts });
  return { client, el, home, onCounts };
};

describe("splitStatuses(バッジ仕分け・引用#9)", () => {
  it("proposed/partial_accept→候補・matched/completed→成立済み・終了3種は捨てる", () => {
    const { candidates, matched } = splitStatuses([
      latch("proposed"),
      latch("partial_accept", { id: "99999999-9999-4999-8999-999999999999" }),
      latch("matched", { id: "88888888-8888-4888-8888-888888888888" }),
      latch("completed", { id: "77777777-7777-4777-8777-777777777777" }),
      latch("rejected", { id: "66666666-6666-4666-8666-666666666666" }),
      latch("expired", { id: "55555555-5555-4555-8555-555555555555" }),
      latch("cancelled", { id: "44444444-4444-4444-8444-444444444444" }),
    ]);
    expect(candidates.map((l) => l.status)).toEqual(["proposed", "partial_accept"]);
    expect(matched.map((l) => l.status)).toEqual(["matched", "completed"]);
  });
});

describe("intentCardHtml(01待機中の意思カード・プロトタイプ01)", () => {
  it("カテゴリ+相対時間・原文引用・条件タグ・期限行・待機中pillを組む", () => {
    const html = intentCardHtml(intent(), NOW);
    expect(html).toContain(`href="#/standby"`); // 対応づけが確定できないときは03へ
    expect(html).toContain("飲み · 12分前に預けた");
    expect(html).toContain("「今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい」");
    expect(html).toContain(">20:00〜<");
    expect(html).toContain(">天文館<");
    expect(html).toContain(">2〜4人<");
    expect(html).toContain(">〜¥5,000<");
    expect(html).toContain("今日 23:59 まで有効");
    expect(html).toContain("待機中");
    expect(html).toContain("静かに待機中");
  });

  it("expires_at nullは「期限なし」", () => {
    const html = intentCardHtml(intent({ expires_at: null }), NOW);
    expect(html).toContain("期限なし");
  });

  it("raw_textはエスケープされる(XSS対策・所有者への表示のみ)", () => {
    const html = intentCardHtml(
      intent({ raw_text: '<img src=x onerror=alert(1)>"&\'' }),
      NOW,
    );
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("候補N件=acc強調カード+04直結(対応づけが確定できるとき)", () => {
    const html = intentCardHtml(intent(), NOW, {
      proposalCount: 2,
      proposalHref: `#/latches/${LATCH_ID}`,
    });
    expect(html).toContain('class="intent-card lift acc"');
    expect(html).toContain("候補 2件");
    expect(html).toContain("候補を見る");
    expect(html).toContain(`href="#/latches/${LATCH_ID}"`);
  });
});

describe("pairCandidateLatch(候補↔Intentの対応づけ・latchesにintent_idsがないため)", () => {
  it("active 1件×候補ありのときだけ対応を確定する(複数件はnull=契約持ち帰り)", () => {
    const one = [intent()];
    expect(pairCandidateLatch(one, [latch("proposed")])).toEqual({
      latch: latch("proposed"),
      intentId: INTENT_ID,
    });
    expect(
      pairCandidateLatch(
        [intent(), intent({ id: "bbbbbbbb-2222-4222-8222-222222222222" })],
        [latch("proposed")],
      ),
    ).toBeNull();
    expect(pairCandidateLatch(one, [])).toBeNull();
  });
});

describe("createHome(01の待機中の意思+バッジ供給)", () => {
  it("loadIntentsはGET /v1/intents?status=activeでカードを描画し待機中バッジへ", async () => {
    const { client, el, home, onCounts } = mountHome(async () => ({
      items: [intent(), intent({ id: "bbbbbbbb-2222-4222-8222-222222222222", raw_text: "年収1,000万円以上なら転職を考える" })],
      next_cursor: null,
    }));
    await home.loadIntents();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/intents?status=active");
    expect(el.intentCards.querySelectorAll(".intent-card").length).toBe(2);
    expect(onCounts).toHaveBeenCalledWith({ standby: 2 });
    expect(el.standbyLine.hidden).toBe(false);
    expect(el.standbyLine.querySelector("[data-standby-count]").textContent).toBe(
      "2件の意思が待機中 — いまは何もしなくて大丈夫",
    );
    expect(el.waitSection.hidden).toBe(false);
  });

  it("0件はヒーロー行とセクションを隠す(プロトタイプに空状態の規定なし)", async () => {
    const { el, home, onCounts } = mountHome(async () => ({ items: [], next_cursor: null }));
    await home.loadIntents();
    expect(el.intentCards.querySelectorAll(".intent-card").length).toBe(0);
    expect(el.standbyLine.hidden).toBe(true);
    expect(el.waitSection.hidden).toBe(true);
    expect(onCounts).toHaveBeenCalledWith({ standby: 0 });
  });

  it("loadはGET /v1/latches?limit=20で候補・成立のバッジだけ供給する(一覧はPhase 3/4)", async () => {
    const { client, el, home, onCounts } = mountHome(async () => ({
      items: [
        latch("proposed"),
        latch("partial_accept", { id: "99999999-9999-4999-8999-999999999999" }),
        latch("matched", { id: "88888888-8888-4888-8888-888888888888" }),
      ],
      next_cursor: null,
    }));
    await home.load();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/latches?limit=20");
    expect(onCounts).toHaveBeenLastCalledWith({ proposals: 2, matched: 1 });
  });

  it("active 1件×候補ありはカードが04直結になる(候補を見る)", async () => {
    const calls = [];
    const { el, home } = mountHome(async (method, path) => {
      calls.push(path);
      if (path === "/v1/intents?status=active") {
        return { items: [intent()], next_cursor: null };
      }
      return { items: [latch("proposed")], next_cursor: null };
    });
    await home.load();
    await home.loadIntents();
    const card = el.intentCards.querySelector(".intent-card");
    expect(card.getAttribute("href")).toBe(`#/latches/${LATCH_ID}`);
    expect(card.textContent).toContain("候補 1件");
  });
});
