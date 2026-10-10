import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { HomeScreen, IntentCard } from "../src/intent/HomeScreen.jsx";
import { pairCandidateLatch, splitStatuses } from "../src/latch/home.js";
import { createFormState } from "../src/intent/state.js";
import { ChromeProvider } from "../src/ui/chrome.jsx";

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

// HomeScreenを共有フォームstateつきで描く(App配線と同じ形)。
// UI v2 Phase 6から01のカード組立はReactコンポーネントのため、旧
// intentCardHtml/createHomeの試験意図=カードの構成・文言・バッジ供給をRTLで踏襲。
const mountHome = (callImpl) => {
  cleanup();
  const client = { call: vi.fn(callImpl) };
  const stateRef = { current: createFormState() };
  const chrome = { current: null };
  const Harness = () => {
    const [s, setS] = useState(stateRef.current);
    const update = (updater) => {
      const next = typeof updater === "function" ? updater(stateRef.current) : updater;
      stateRef.current = next;
      setS(next);
    };
    return (
      <ChromeProvider>
        <HomeScreen client={client} state={s} getState={() => stateRef.current} setState={update} />
      </ChromeProvider>
    );
  };
  const utils = render(<Harness />);
  return { client, utils };
};

const cards = () => [...document.querySelectorAll(".intent-card")];

afterEach(() => {
  cleanup();
});

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

describe("IntentCard(01待機中の意思カード・プロトタイプ01)", () => {
  const mountCard = (cardIntent, options = {}) => {
    cleanup();
    return render(
      <IntentCard intent={cardIntent} nowIso={NOW} proposalCount={options.proposalCount ?? 0} proposalHref={options.proposalHref ?? null} />,
    );
  };

  it("カテゴリ+相対時間・原文引用・条件タグ・期限行・待機中pillを組む", () => {
    mountCard(intent());
    const card = document.querySelector(".intent-card");
    expect(card.getAttribute("href")).toBe("#/standby"); // 対応づけが確定できないときは03へ
    expect(card.textContent).toContain("飲み · 12分前に預けた");
    expect(card.textContent).toContain("「今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい」");
    expect([...card.querySelectorAll(".intent-tag")].map((t) => t.textContent)).toEqual([
      "20:00〜",
      "天文館",
      "2〜4人",
      "〜¥5,000",
    ]);
    expect(card.textContent).toContain("今日 23:59 まで有効");
    expect(card.textContent).toContain("待機中");
    expect(card.textContent).toContain("静かに待機中");
  });

  it("expires_at nullは「期限なし」", () => {
    mountCard(intent({ expires_at: null }));
    expect(document.querySelector(".intent-card").textContent).toContain("期限なし");
  });

  it("raw_textはテキストノードとして組まれる(XSS対策・所有者への表示のみ)", () => {
    mountCard(intent({ raw_text: '<img src=x onerror=alert(1)>"&\'' }));
    const card = document.querySelector(".intent-card");
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector(".intent-quote").textContent).toBe(
      '「<img src=x onerror=alert(1)>"&\'」',
    );
  });

  it("候補N件=acc強調カード+04直結(対応づけが確定できるとき)", () => {
    mountCard(intent(), { proposalCount: 2, proposalHref: `#/latches/${LATCH_ID}` });
    const card = document.querySelector(".intent-card");
    expect(card.className).toContain("acc");
    expect(card.getAttribute("href")).toBe(`#/latches/${LATCH_ID}`);
    expect(card.textContent).toContain("候補 2件");
    expect(card.textContent).toContain("候補を見る");
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

describe("HomeScreen(01の待機中の意思+バッジ供給)", () => {
  it("GET /v1/intents?status=activeでカードを描画し待機中バッジへ", async () => {
    mountHome(async () => ({
      items: [intent(), intent({ id: "bbbbbbbb-2222-4222-8222-222222222222", raw_text: "年収1,000万円以上なら転職を考える" })],
      next_cursor: null,
    }));
    await waitFor(() => expect(cards().length).toBe(2));
    expect(document.querySelector("#standbyLine").hidden).toBe(false);
    expect(document.querySelector("[data-standby-count]").textContent).toBe(
      "2件の意思が待機中 — いまは何もしなくて大丈夫",
    );
    expect(document.querySelector("#waitSection").hidden).toBe(false);
  });

  it("0件はヒーロー行とセクションを隠す(プロトタイプに空状態の規定なし)", async () => {
    mountHome(async () => ({ items: [], next_cursor: null }));
    await act(async () => {});
    await waitFor(() => {
      expect(document.querySelector("#standbyLine").hidden).toBe(true);
      expect(document.querySelector("#waitSection").hidden).toBe(true);
    });
    expect(cards().length).toBe(0);
  });

  it("GET /v1/latches?limit=20で候補・成立のバッジだけ供給する(一覧はD-11)", async () => {
    const calls = [];
    mountHome(async (method, path) => {
      calls.push(path);
      return { items: [], next_cursor: null };
    });
    await act(async () => {});
    await waitFor(() => {
      expect(calls).toContain("/v1/latches?limit=20");
      expect(calls).toContain("/v1/intents?status=active");
    });
  });

  it("active 1件×候補ありはカードが04直結になる(候補を見る)", async () => {
    mountHome(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent()], next_cursor: null };
      }
      return { items: [latch("proposed")], next_cursor: null };
    });
    await waitFor(() => expect(cards().length).toBe(1));
    await waitFor(() => {
      expect(cards()[0].getAttribute("href")).toBe(`#/latches/${LATCH_ID}`);
      expect(cards()[0].textContent).toContain("候補 1件");
    });
  });
});
