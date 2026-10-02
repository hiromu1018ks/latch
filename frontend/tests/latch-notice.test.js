import { describe, expect, it } from "vitest";
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
