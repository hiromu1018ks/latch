import { describe, expect, it, vi } from "vitest";
import { NO_LATCH_TITLE } from "../src/latch/texts.js";
import {
  createHome,
  latchCardHtml,
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

const mountHome = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const el = {
    candidateList: document.createElement("div"),
    matchedList: document.createElement("div"),
    moreButton: document.createElement("button"),
    intentCount: document.createElement("button"),
    intentList: document.createElement("div"),
  };
  el.moreButton.hidden = true;
  const home = createHome({ client, el });
  return { client, el, home };
};

describe("splitStatuses(design §2.2・引用#9)", () => {
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

describe("latchCardHtml", () => {
  it("カード行は#/latches/{id}へのリンク", () => {
    const html = latchCardHtml(latch("proposed"), "2026-10-01T09:00:00+09:00");
    expect(html).toContain(`href="#/latches/${LATCH_ID}"`);
    expect(html).toContain("2026-10-01 20:00 天文館周辺"); // 見出し=日時+場所
    expect(html).toContain("一致度 高");
  });
});

describe("createHome(design §2.2)", () => {
  it("loadはGET /v1/latches?limit=20を1回・itemsをstatusで仕分け描画", async () => {
    const { client, el, home } = mountHome(async () => ({
      items: [latch("proposed"), latch("matched", { id: "88888888-8888-4888-8888-888888888888" })],
      next_cursor: null,
    }));
    await home.load();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/latches?limit=20");
    expect(el.candidateList.querySelectorAll(".latch-card").length).toBe(1);
    expect(el.matchedList.querySelectorAll(".latch-card").length).toBe(1);
  });

  it("終了済み(rejected/expired/cancelled)はどちらのセクションにも出ない", async () => {
    const { el, home } = mountHome(async () => ({
      items: [latch("rejected"), latch("expired", { id: "55555555-5555-4555-8555-555555555555" }), latch("cancelled", { id: "44444444-4444-4444-8444-444444444444" })],
      next_cursor: null,
    }));
    await home.load();
    expect(el.candidateList.querySelectorAll(".latch-card").length).toBe(0);
    expect(el.matchedList.querySelectorAll(".latch-card").length).toBe(0);
  });

  it("next_cursorが残れば「もっと見る」を表示し追頁でcursorを渡す", async () => {
    const pages = [
      { items: [latch("proposed")], next_cursor: "c1" },
      { items: [latch("proposed", { id: "99999999-9999-4999-8999-999999999999" })], next_cursor: null },
    ];
    const { client, el, home } = mountHome(async () => pages.shift());
    await home.load();
    expect(el.moreButton.hidden).toBe(false);
    await home.loadMore();
    expect(client.call.mock.calls[1]).toEqual([
      "GET",
      "/v1/latches?limit=20&cursor=c1",
    ]);
    expect(el.candidateList.querySelectorAll(".latch-card").length).toBe(2);
    expect(el.moreButton.hidden).toBe(true); // 終端
  });

  it("next_cursor=nullなら「もっと見る」は非表示のまま", async () => {
    const { el, home } = mountHome(async () => ({ items: [], next_cursor: null }));
    await home.load();
    expect(el.moreButton.hidden).toBe(true);
  });

  it("Intent N件: active数を反映しpopoverにactive一覧の行(カテゴリ・時刻)", async () => {
    const intent = (primary) => ({
      id: "aaaaaaa1-1111-4111-8111-111111111111",
      status: "active",
      structured_intent: {
        category: { primary, secondary: null },
        time: { start: "2026-10-01T20:00:00+09:00", end: null },
      },
      expires_at: null,
    });
    const { client, el, home } = mountHome(async (method, path) => {
      if (path.includes("status=active")) {
        return { items: [intent("drinking"), intent("meal")], next_cursor: null };
      }
      return { items: [], next_cursor: null }; // draft
    });
    await home.loadIntents();
    expect(client.call.mock.calls[0]).toEqual([
      "GET",
      "/v1/intents?status=active",
    ]);
    expect(el.intentCount.textContent).toContain("Intent");
    expect(el.intentCount.textContent).toContain("2件");
    expect(el.intentList.querySelectorAll(".intent-row").length).toBe(2);
    expect(el.intentList.textContent).toContain("飲み"); // カテゴリラベル
  });

  it("下書きがあれば件数のみ添える(表示のみ)", async () => {
    const { el, home } = mountHome(async (method, path) => {
      if (path.includes("status=active")) {
        return { items: [], next_cursor: null };
      }
      return {
        items: [
          { id: "d1", status: "draft", structured_intent: {} },
          { id: "d2", status: "draft", structured_intent: {} },
        ],
        next_cursor: null,
      };
    });
    await home.loadIntents();
    expect(el.intentCount.textContent).toContain("0件"); // カウントはactive数
    expect(el.intentList.textContent).toContain("下書き 2件");
  });

  it("候補セクションの空状態は「まだ提案はありません」", async () => {
    const { el, home } = mountHome(async () => ({ items: [], next_cursor: null }));
    await home.load();
    expect(el.candidateList.textContent).toContain(NO_LATCH_TITLE);
  });

  it("hidden_until_match形の候補カードは「条件が合う候補があります」", async () => {
    const { el, home } = mountHome(async () => ({
      items: [latch("proposed", { proposal: { headcount: 2, match_level: "medium" } })],
      next_cursor: null,
    }));
    await home.load();
    expect(el.candidateList.textContent).toContain("条件が合う候補があります");
    expect(el.candidateList.textContent).not.toContain("天文館");
  });
});
