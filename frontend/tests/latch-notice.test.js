import { describe, expect, it, vi } from "vitest";
import {
  ATTENDANCE_QUESTION,
  CLOSED_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  MATCH_NOTICE_TEXT,
  NEARBY_NOTICE_TEXT,
  NOTICE_EMPTY_NOTE,
  NOTICE_EMPTY_TITLE,
  NOTICE_FALLBACK_TEXT,
  PROPOSAL_NOTICE_TITLE,
} from "../src/latch/texts.js";
import {
  notificationHref,
  notificationLines,
  noticeTimeText,
} from "../src/latch/view.js";
import { createNotice } from "../src/latch/notice.js";

// API応答のfixture(§2-1のNotificationItem型・proposalは全フィールド版)
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
const minimalProposal = { headcount: 2, match_level: "low" }; // hidden_until_match形
const notificationLatch = (status, overrides = {}, proposal = fullProposal) => ({
  id: LATCH_ID,
  status,
  response_deadline: "2026-10-02T12:00:00+09:00",
  expires_at: "2026-10-05T12:00:00+09:00",
  completed_at: null,
  proposal,
  ...overrides,
});
const NOW = "2026-10-01T09:00:00+09:00"; // 残時間27時間→「あと1日と3時間で締切」
const item = (type, latch, overrides = {}) => ({
  id: "n1",
  type,
  latch_id: latch?.id ?? null,
  read_at: null,
  created_at: "2026-10-01T09:00:00+09:00",
  latch: latch ?? null,
  ...overrides,
});

describe("notificationLines(design §2.3のマトリクス)", () => {
  it("proposal×終了3種(expired/rejected/cancelled)はCLOSED_TEXTの1行のみ(一文統一)", () => {
    for (const status of ["expired", "rejected", "cancelled"]) {
      expect(notificationLines(item("proposal", notificationLatch(status)), NOW))
        .toEqual([CLOSED_TEXT]);
    }
  });

  it("proposal×proposed全形: 見出し+条件サマリ+一致度文(03 §4様式)", () => {
    expect(notificationLines(item("proposal", notificationLatch("proposed")), NOW))
      .toEqual([
        PROPOSAL_NOTICE_TITLE,
        "2026-10-01 20:00",
        "天文館周辺",
        "2人",
        "飲み",
        MATCH_NOTICE_TEXT,
      ]);
  });

  it("proposal×proposed最小形(hidden): 条件サマリなし+残時間(期限はresponse_deadline)", () => {
    expect(
      notificationLines(
        item("proposal", notificationLatch("proposed", {}, minimalProposal)),
        NOW,
      ),
    ).toEqual([
      HIDDEN_PROPOSAL_TEXT,
      MATCH_NOTICE_TEXT,
      "あと1日と3時間で締切", // 2026-10-01 09:00→10-02 12:00
    ]);
  });

  it("proposal×matched/completed: 見出し+サマリ+STATUS_TEXTバッジ・回答を促す一文は出さない", () => {
    const matched = notificationLines(
      item("proposal", notificationLatch("matched")), NOW);
    expect(matched.at(-1)).toBe("成立済み"); // STATUS_TEXT.matched
    expect(matched).not.toContain(MATCH_NOTICE_TEXT);
    expect(matched).not.toContain(CLOSED_TEXT);
    const completed = notificationLines(
      item("proposal", notificationLatch("completed")), NOW);
    expect(completed.at(-1)).toBe("完了"); // STATUS_TEXT.completed
  });

  it("proposal×matched最小形: 見出しはHIDDEN_PROPOSAL_TEXT", () => {
    const lines = notificationLines(
      item("proposal", notificationLatch("matched", {}, minimalProposal)), NOW);
    expect(lines[0]).toBe(HIDDEN_PROPOSAL_TEXT);
    expect(lines.at(-1)).toBe("成立済み");
  });

  it("nearby_candidate: 存在文言のみ(statusによらず・サマリ・一致度・人数を出さない)", () => {
    for (const status of ["candidate", "expired"]) {
      expect(notificationLines(item("nearby_candidate", notificationLatch(status)), NOW))
        .toEqual([NEARBY_NOTICE_TEXT]);
    }
  });

  it("attendance_request: 設問のみ(回答導線は詳細画面・FR-46)", () => {
    expect(notificationLines(item("attendance_request", notificationLatch("completed")), NOW))
      .toEqual([ATTENDANCE_QUESTION]);
  });

  it("latch=null(防御): フォールバック文言", () => {
    expect(notificationLines(item("proposal", null), NOW))
      .toEqual([NOTICE_FALLBACK_TEXT]);
  });
});

describe("notificationHref(design §2.3・引用#20)", () => {
  it("nearby行とlatch=null行はnull(candidateはGET /v1/latches対象外のため)", () => {
    expect(notificationHref(item("nearby_candidate", notificationLatch("candidate")))).toBeNull();
    expect(notificationHref(item("proposal", null))).toBeNull();
  });

  it("proposal行(終了含む)とattendance行は#/latches/{id}へ", () => {
    expect(notificationHref(item("proposal", notificationLatch("cancelled"))))
      .toBe(`#/latches/${LATCH_ID}`);
    expect(notificationHref(item("proposal", notificationLatch("proposed"))))
      .toBe(`#/latches/${LATCH_ID}`);
    expect(notificationHref(item("attendance_request", notificationLatch("completed"))))
      .toBe(`#/latches/${LATCH_ID}`);
  });
});

describe("noticeTimeText(created_atのJST書式)", () => {
  it("+09:00付きISOをM月D日 HH:MMへ(zero埋め)", () => {
    expect(noticeTimeText("2026-10-01T09:05:00+09:00")).toBe("10月1日 09:05");
  });

  it("UTC入力はJSTへ変換(00:05Z=09:05+09:00)", () => {
    expect(noticeTimeText("2026-10-01T00:05:00Z")).toBe("10月1日 09:05");
  });
});

const mountNotice = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ items: [], next_cursor: null }))) };
  const el = {
    list: document.createElement("div"),
    moreButton: document.createElement("button"),
    dot: document.createElement("span"),
  };
  el.moreButton.hidden = true;
  el.dot.hidden = true;
  const notice = createNotice({ client, el });
  return { client, el, notice };
};

describe("createNotice(design §2.1〜§2.2)", () => {
  it("preloadはGET /v1/notifications?limit=20を1回・未読があればドット表示+行描画", async () => {
    const { client, el, notice } = mountNotice(async () => ({
      items: [item("proposal", notificationLatch("proposed"))],
      next_cursor: null,
    }));
    await notice.preload();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/notifications?limit=20");
    expect(el.dot.hidden).toBe(false);
    expect(el.list.querySelectorAll(".notice-item").length).toBe(1);
  });

  it("全件既読・0件ならドットは非表示のまま", async () => {
    const { el, notice } = mountNotice(async () => ({
      items: [
        item("proposal", notificationLatch("proposed"), {
          read_at: "2026-10-01T08:00:00+09:00",
        }),
      ],
      next_cursor: null,
    }));
    await notice.preload();
    expect(el.dot.hidden).toBe(true);
  });

  it("openは未読行へだけPOST readする(既読行には呼ばない)", async () => {
    const { client, notice } = mountNotice(async (method) => {
      if (method === "POST") return null; // 204
      return {
        items: [
          item("proposal", notificationLatch("proposed")), // 未読
          item("proposal", notificationLatch("proposed"), {
            id: "n2",
            read_at: "2026-10-01T08:00:00+09:00",
          }),
        ],
        next_cursor: null,
      };
    });
    await notice.open();
    const posts = client.call.mock.calls.filter(([method]) => method === "POST");
    expect(posts).toEqual([["POST", "/v1/notifications/n1/read"]]);
  });

  it("read完了後にドットを再判定する(全未読→既読化で消灯)", async () => {
    const { el, notice } = mountNotice(async (method) => {
      if (method === "POST") return null;
      return { items: [item("proposal", notificationLatch("proposed"))], next_cursor: null };
    });
    await notice.open();
    expect(el.dot.hidden).toBe(true); // 開いた=見た
  });

  it("POST readに失敗した行は握る(ドット点灯維持・次回開いた時の未読対象)", async () => {
    const { el, notice } = mountNotice(async (method) => {
      if (method === "POST") throw new Error("x");
      return { items: [item("proposal", notificationLatch("proposed"))], next_cursor: null };
    });
    await notice.open(); // throwされてもopen自体は正常終了
    expect(el.dot.hidden).toBe(false);
  });

  it("next_cursorが残れば「もっと見る」を表示し追頁でcursorを渡す", async () => {
    const pages = [
      { items: [item("proposal", notificationLatch("proposed"))], next_cursor: "c1" },
      {
        items: [
          item("proposal", notificationLatch("proposed"), {
            id: "n2",
            read_at: "2026-10-01T08:00:00+09:00",
          }),
        ],
        next_cursor: null,
      },
    ];
    const { client, el, notice } = mountNotice(async (method) => {
      if (method === "POST") return null;
      return pages.shift();
    });
    await notice.open();
    expect(el.moreButton.hidden).toBe(false);
    await notice.loadMore();
    expect(client.call.mock.calls.at(-1)).toEqual([
      "GET",
      "/v1/notifications?limit=20&cursor=c1",
    ]);
    expect(el.list.querySelectorAll(".notice-item").length).toBe(2);
    expect(el.moreButton.hidden).toBe(true);
  });

  it("0件で空状態文言(まだ新しい候補はありません)", async () => {
    const { el, notice } = mountNotice();
    await notice.open();
    expect(el.list.textContent).toContain(NOTICE_EMPTY_TITLE);
    expect(el.list.textContent).toContain(NOTICE_EMPTY_NOTE);
  });

  it("取得失敗は前回表示を維持(空状態も出さない)", async () => {
    let fail = false;
    const { el, notice } = mountNotice(async () => {
      if (fail) throw new Error("x");
      return { items: [item("proposal", notificationLatch("proposed"))], next_cursor: null };
    });
    await notice.preload();
    const before = el.list.innerHTML;
    fail = true;
    await notice.open();
    expect(el.list.innerHTML).toBe(before);
  });

  it("preload後の初回openはGETしない(二重取得なし)・nearby行はリンク化しない", async () => {
    const { client, el, notice } = mountNotice(async (method) => {
      if (method === "POST") return null;
      return {
        items: [
          item("nearby_candidate", notificationLatch("candidate"), {
            read_at: "2026-10-01T08:00:00+09:00",
          }),
        ],
        next_cursor: null,
      };
    });
    await notice.preload();
    await notice.open();
    expect(client.call).toHaveBeenCalledTimes(1); // preloadのGETのみ
    expect(el.list.querySelectorAll("a.notice-item").length).toBe(0); // nearbyは<div>
    expect(el.list.querySelectorAll(".notice-item").length).toBe(1);
  });
});
