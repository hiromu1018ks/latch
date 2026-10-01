import { describe, expect, it } from "vitest";
import {
  ANSWERED_TEXT,
  CHAT_UNAVAILABLE_TEXT,
  CLOSED_TEXT,
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  RESPONSE_BUTTONS,
} from "../src/latch/texts.js";
import {
  closedText,
  conditionSummaryLines,
  escapeHtml,
  groupNeedText,
  isMinimalProposal,
  latchMode,
  matchLevelText,
  messageSide,
  remainingMs,
  remainingTimeText,
  senderName,
} from "../src/latch/view.js";

// API応答のfixture(§2-1のLatchDetail型・proposalは全フィールド版)
const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: "軽く飲めるお店",
  budget: { max: 5000 },
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "low" }; // hidden_until_match形
const base = {
  id: "11111111-1111-4111-8111-111111111111",
  status: "proposed",
  response_deadline: "2026-10-01T12:00:00+09:00",
  expires_at: "2026-10-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal: fullProposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
};
const latch = (overrides = {}, proposal = fullProposal) => ({
  ...base,
  proposal,
  ...overrides,
});
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const participants = [
  { user_id: ME, display_name: "自分", profile: {} },
  { user_id: PEER, display_name: "相手", profile: {} },
];

describe("latchMode(status→3姿・design §2.3)", () => {
  it("proposed/partial_accept→proposal・matched/completed→matched・終了3種→closed", () => {
    expect(latchMode(latch({ status: "proposed" }))).toBe("proposal");
    expect(latchMode(latch({ status: "partial_accept" }))).toBe("proposal");
    expect(latchMode(latch({ status: "matched" }))).toBe("matched");
    expect(latchMode(latch({ status: "completed" }))).toBe("matched");
    expect(latchMode(latch({ status: "rejected" }))).toBe("closed");
    expect(latchMode(latch({ status: "expired" }))).toBe("closed");
    expect(latchMode(latch({ status: "cancelled" }))).toBe("closed");
  });
});

describe("visibility分岐(design §2.4・引用#5)", () => {
  it("proposalのtime_summaryキー有無で最小形を判定する", () => {
    expect(isMinimalProposal(latch({}, minimalProposal))).toBe(true);
    expect(isMinimalProposal(latch({}, fullProposal))).toBe(false);
  });

  it("全フィールド版の条件サマリ行(日時/場所/人数/カテゴリ/予算)", () => {
    expect(conditionSummaryLines(latch())).toEqual([
      "2026-10-01 20:00",
      "天文館周辺",
      "2人",
      "軽く飲めるお店", // category_secondary優先
      "ひとり5,000円まで",
    ]);
  });

  it("budget nullは予算行なし・secondary nullはprimaryの日本語ラベル", () => {
    expect(
      conditionSummaryLines(
        latch({}, {
          ...fullProposal,
          category_secondary: null,
          budget: null,
        }),
      ),
    ).toEqual(["2026-10-01 20:00", "天文館周辺", "2人", "飲み"]);
  });

  it("最小形はnull(条件サマリを組まない)", () => {
    expect(conditionSummaryLines(latch({}, minimalProposal))).toBeNull();
  });

  it("area_name nullは行を組まない(「null」という文字列を出さない)", () => {
    expect(
      conditionSummaryLines(latch({}, { ...fullProposal, area_name: null })),
    ).toEqual(["2026-10-01 20:00", "2人", "軽く飲めるお店", "ひとり5,000円まで"]);
  });
});

describe("一致度(design §2.4・生スコアは出さない)", () => {
  it("match_level 3値の文言", () => {
    expect(matchLevelText(latch({}, { ...minimalProposal, match_level: "high" }))).toBe("高");
    expect(matchLevelText(latch({}, { ...minimalProposal, match_level: "medium" }))).toBe("中");
    expect(matchLevelText(latch({}, minimalProposal))).toBe("低");
  });
});

describe("残時間書式(design §2.4・deadline=2026-10-01T12:00+09:00基準)", () => {
  it("24時間超は「あとN日とM時間で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-09-28T09:00:00+09:00"))
      .toBe("あと3日と3時間で締切");
  });

  it("24時間超・時間0は「あとN日で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-09-29T12:00:00+09:00"))
      .toBe("あと2日で締切");
  });

  it("1〜24時間は「あとN時間M分で締切」(designの例と同形)", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T10:37:00+09:00"))
      .toBe("あと1時間23分で締切");
  });

  it("1〜24時間・分0は「あとN時間で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T10:00:00+09:00"))
      .toBe("あと2時間で締切");
  });

  it("1時間未満は「あとN分で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T11:20:00+09:00"))
      .toBe("あと40分で締切");
  });

  it("1分未満は「まもなく締切」(秒は表示しない)", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T11:59:30+09:00"))
      .toBe("まもなく締切");
  });

  it("経過後・ちょうど0は「締切」・remainingMsは負", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T13:00:00+09:00"))
      .toBe("締切");
    expect(remainingTimeText(base.response_deadline, "2026-10-01T12:00:00+09:00"))
      .toBe("締切");
    expect(remainingMs(base.response_deadline, "2026-10-01T13:00:00+09:00"))
      .toBeLessThanOrEqual(0);
  });
});

describe("グループ必要人数(design §2.4・引用#4)", () => {
  it("is_group+残回答>0のときのみ「あとN人の回答が必要」", () => {
    expect(groupNeedText(latch({ is_group: true, remaining_responses: 2 })))
      .toBe("あと2人の回答が必要");
    expect(groupNeedText(latch({ is_group: false, remaining_responses: 1 })))
      .toBeNull(); // 1対1は現況表示なし
    expect(groupNeedText(latch({ is_group: true, remaining_responses: 0 })))
      .toBeNull(); // 成立済み
  });
});

describe("不成立の二値化(design §2.3・引用#9)", () => {
  it("my_response=no/deferは自身の操作履歴", () => {
    expect(closedText(latch({ status: "cancelled", my_response: "no" })))
      .toBe("辞退しました");
    expect(closedText(latch({ status: "rejected", my_response: "defer" })))
      .toBe("今回は見送りました");
  });

  it("それ以外(相手の回答・期限切れ・ブロック等)は統一文言", () => {
    expect(closedText(latch({ status: "cancelled", my_response: null })))
      .toBe(CLOSED_TEXT);
    expect(closedText(latch({ status: "expired", my_response: "yes" })))
      .toBe(CLOSED_TEXT); // 自分は参加しても不成立は統一文言
  });
});

describe("メッセージの左右判定と送信者名(design §2.6)", () => {
  it("sender_id===meはmine・それ以外はtheirs・名前はparticipantsから解決", () => {
    const mine = { sender_id: ME, body: "こんにちは" };
    const theirs = { sender_id: PEER, body: "はじめまして" };
    expect(messageSide(mine, ME)).toBe("mine");
    expect(messageSide(theirs, ME)).toBe("theirs");
    expect(senderName(PEER, participants)).toBe("相手");
    expect(senderName("44444444-4444-4444-8444-444444444444", participants))
      .toBe(""); // 不在(グループ外・防御)
  });
});

describe("escapeHtml(XSS対策・API応答由来文字列)", () => {
  it("HTML特殊文字をエスケープする", () => {
    expect(escapeHtml('<script>alert("x")</script>'))
      .toBe("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
    expect(escapeHtml("a&b<c>d'e")).toBe("a&amp;b&lt;c&gt;d&#39;e");
  });
});

describe("texts.jsの定数ピン(design §2.8・ws-8参照)", () => {
  it("統一文言・D-23文言・429文言", () => {
    expect(CLOSED_TEXT).toBe("この提案は成立しませんでした");
    expect(CHAT_UNAVAILABLE_TEXT).toBe("このチャットは利用できません");
    expect(RATE_LIMIT_TEXT)
      .toBe("操作が集中しています。少し時間をおいてもう一度お試しください。");
  });

  it("回答3択と回答済み文言・通報理由4値コード", () => {
    expect(RESPONSE_BUTTONS).toEqual([
      ["yes", "参加する"],
      ["defer", "今回は見送る"],
      ["no", "辞退する"],
    ]);
    expect(ANSWERED_TEXT).toEqual({
      yes: "参加します",
      defer: "今回は見送りました",
      no: "辞退しました",
    });
    expect(REPORT_REASON_OPTIONS).toEqual([
      ["inappropriate_content", "不適切な内容"],
      ["unpleasant_behavior", "不快な対応"],
      ["suspected_impersonation", "なりすまし疑い"],
      ["other", "その他"],
    ]);
  });
});
