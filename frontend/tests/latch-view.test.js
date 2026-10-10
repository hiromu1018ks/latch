import { describe, expect, it } from "vitest";
import {
  ANSWERED_TEXT,
  CHAT_UNAVAILABLE_TEXT,
  CLOSED_TEXT,
  RATE_LIMIT_TEXT,
  REASON_CHIPS,
  REPORT_REASON_OPTIONS,
  RESPONSE_BUTTONS,
} from "../src/latch/texts.js";
import {
  closedText,
  conditionSummaryLines,
  countdownText,
  escapeHtml,
  fitRows,
  formatYen,
  groupNeedText,
  hhmmssText,
  isMinimalProposal,
  joinedWaitText,
  latchMode,
  matchLevelText,
  messageSide,
  pickMyIntent,
  proposalStateOf,
  proposalTimeParts,
  remainingMs,
  remainingTimeText,
  senderName,
  whoLineText,
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

  it("回答2択(D-05で辞退を廃止)と回答済み文言・通報理由4値コード", () => {
    expect(RESPONSE_BUTTONS).toEqual([
      ["yes", "参加する"],
      ["defer", "今回は見送る"],
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
    expect(REASON_CHIPS).toEqual([
      ["time", "時間が合わない"],
      ["place", "場所が合わない"],
      ["size", "人数が合わない"],
      ["mood", "今日は気分じゃない"],
    ]);
  });
});

describe("04の4状態(proposalStateOf・D-05〜D-07)", () => {
  it("open/joined/allIn/declinedをmy_responseとremaining_responsesから出す", () => {
    expect(proposalStateOf(latch({ my_response: null }))).toBe("open");
    expect(
      proposalStateOf(latch({ my_response: "yes", remaining_responses: 1 })),
    ).toBe("joined");
    expect(
      proposalStateOf(latch({ status: "matched", my_response: "yes", remaining_responses: 0 })),
    ).toBe("allIn");
    expect(proposalStateOf(latch({ my_response: "defer" }))).toBe("declined");
  });
});

describe("04の表示純関数(プロトタイプ04)", () => {
  it("proposalTimePartsはtime_summaryから時刻と日付文を出す", () => {
    expect(proposalTimeParts("2026-10-10 20:30", "2026-10-10T12:00:00+09:00"))
      .toEqual({ clock: "20:30", date: "今夜 · 10/10（土）" });
    expect(proposalTimeParts("2026-10-11 20:30", "2026-10-10T12:00:00+09:00"))
      .toEqual({ clock: "20:30", date: "明日 · 10/11（日）" });
    expect(proposalTimeParts("2026-10-12 20:30", "2026-10-10T12:00:00+09:00").date)
      .toBe("10/12（月）");
    expect(proposalTimeParts("うまくない", "2026-10-10T12:00:00+09:00")).toBeNull();
  });

  it("countdownTextは分:秒・hhmmssTextはHH:MM:SS(負は0固定)", () => {
    expect(countdownText(27 * 60 * 1000 + 48_000)).toBe("27:48");
    expect(countdownText(0)).toBe("0:00");
    expect(countdownText(-5)).toBe("0:00");
    expect(hhmmssText(17140_000)).toBe("04:45:40");
    expect(hhmmssText(-1)).toBe("00:00:00");
  });

  it("formatYenは¥+3桁区切り", () => {
    expect(formatYen(4000)).toBe("¥4,000");
  });

  it("whoLineTextは状態ごとの一文(プロトタイプ04のwhoLine)", () => {
    expect(whoLineText("open", latch())).toBe("あなた ＋ まだ見えない1人");
    expect(whoLineText("joined", latch())).toBe("あなた（参加）＋ 回答待ちの1人");
    expect(whoLineText("allIn", latch())).toBe("2人全員が参加");
    expect(whoLineText("declined", latch())).toBe("あなた ＋ まだ見えない1人");
  });

  it("joinedWaitTextは残回答から現況文を組む(回答済み0は括弧なし)", () => {
    expect(joinedWaitText(latch({ remaining_responses: 1 })))
      .toBe("ほかの1人の回答を待っています。全員がそろったら、お知らせします。");
    expect(
      joinedWaitText(
        latch({ is_group: true, remaining_responses: 1 }, { ...fullProposal, headcount: 3 }),
      ),
    ).toBe("ほかの2人の回答を待っています（1人が回答済み）。全員がそろったら、お知らせします。");
  });
});

describe("照合表(fitRows・自分の条件×候補)", () => {
  const myIntent = {
    structured_intent: {
      category: { primary: "drinking", secondary: "軽く飲みたい" },
      time: { start: "2026-10-10T20:00:00+09:00", end: null },
      location: { name: "天文館", radius_m: null },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
      participants_includes_owner: true,
    },
  };
  const NOW = "2026-10-10T12:00:00+09:00";

  it("5行を自分の条件→候補で組む(✓は双方の値が揃った行)", () => {
    const rows = fitRows(myIntent, latch(), NOW);
    expect(rows.map((r) => r.label)).toEqual(["時間", "場所", "人数", "予算", "目的"]);
    expect(rows[0]).toEqual({ label: "時間", mine: "今日20:00 以降", theirs: "20:00 開始", ok: true });
    expect(rows[1]).toEqual({ label: "場所", mine: "天文館", theirs: "天文館周辺", ok: true });
    expect(rows[2].mine).toBe("2〜4人（あなたを含む）");
    expect(rows[2].theirs).toBe("2人");
    expect(rows[3]).toEqual({ label: "予算", mine: "5,000円まで", theirs: "5,000円前後", ok: true });
    expect(rows[4]).toEqual({ label: "目的", mine: "軽く飲みたい", theirs: "軽く飲めるお店", ok: true });
  });

  it("自分のIntentが特定できない行は「—」でok=false(見出し文言を替える)", () => {
    const rows = fitRows(null, latch(), NOW);
    expect(rows.every((r) => r.mine === "—" && r.ok === false)).toBe(true);
  });

  it("pickMyIntentは値マッチングで自分のIntentを特定する(該当なしはnull)", () => {
    const intents = [
      myIntent,
      {
        structured_intent: {
          category: { primary: "activity", secondary: null },
          time: { start: "2026-10-10T20:00:00+09:00", end: null },
          location: { name: "博多駅", radius_m: null },
          budget: { max: 2000, currency: "JPY" },
          participants: { min: 2, max: 4 },
        },
      },
    ];
    expect(pickMyIntent(intents, latch())).toBe(myIntent);
    expect(pickMyIntent([intents[1]], latch())).toBeNull();
  });
});

// --- UI v2 Phase 4追記: 05成立・チャットの純関数 ------------------------------
import {
  AVATAR_COLORS,
  cancelDialogLines,
  chatHeadText,
  chatMembers,
  latchedRows,
  latchedTags,
} from "../src/latch/view.js";

const ME_ID = "22222222-2222-4222-8222-222222222222";
const PEER_ID = "33333333-3333-4333-8333-333333333333";
const THIRD_ID = "44444444-4444-4444-8444-444444444444";

describe("05の純関数(chatMembers・chatHeadText・タグ・dl・取消dialog)", () => {
  const matched = latch(
    {
      status: "matched",
      participants: [
        { user_id: PEER_ID, display_name: "ゆうき", profile: {} },
        { user_id: THIRD_ID, display_name: "Mika", profile: {} },
        { user_id: ME_ID, display_name: "自見", profile: {} },
      ],
    },
    { ...fullProposal, headcount: 3 },
  );

  it("chatMembers: 他者は参加順でパレット色・自分は末尾にme", () => {
    const members = chatMembers(matched, ME_ID);
    expect(members.map((m) => m.name)).toEqual(["ゆうき", "Mika", "自見"]);
    expect(members.map((m) => m.color)).toEqual([
      AVATAR_COLORS[0],
      AVATAR_COLORS[1],
      null, // 自分はme様式(色なし)
    ]);
    expect(members[2].me).toBe(true);
    expect(members[0].initial).toBe("ゆ");
  });

  it("chatHeadText: 「今夜の3人」+「20:00 · 天文館周辺」(日付語は実行日で替わる)", () => {
    const head = chatHeadText(matched);
    expect(head.title).toMatch(/の3人$/);
    expect(head.sub).toBe("20:00 · 天文館周辺");
    // 時刻の取れない形は「N人のチャット」へ落ちる(minimal形はheadcount 2)
    const bare = chatHeadText(latch({ time_summary: null, area_name: null }, minimalProposal));
    expect(bare.title).toBe("2人のチャット");
    expect(bare.sub).toBe("");
  });

  it("latchedTags: 時間(mono)・場所・人数・予算(mono・前後)。欠けは組まない", () => {
    expect(latchedTags(matched)).toEqual([
      { text: "20:00〜", mono: true },
      { text: "天文館周辺", mono: false },
      { text: "3人", mono: false },
      { text: "¥5,000前後", mono: true },
    ]);
    const sparse = latchedTags(latch({ time_summary: null, area_name: null }, minimalProposal));
    expect(sparse).toEqual([{ text: "2人", mono: false }]);
  });

  it("latchedRows: 時間({日付語} 20:00〜)・場所(subつき)・人数・予算(1人 5,000円前後)", () => {
    const rows = latchedRows(matched);
    expect(rows[0].label).toBe("時間");
    expect(rows[0].value).toMatch(/ 20:00〜$/); // 日付語は実行日で替わる(今夜/明日/M/D（dow）)
    expect(rows[1].label).toBe("場所");
    expect(rows[1].sub).toBe(true);
    expect(rows[2]).toEqual({ label: "人数", value: "3人" });
    expect(rows[3]).toEqual({ label: "予算", value: "1人 5,000円前後" });
  });

  it("cancelDialogLines: 影響3行(ほかのメンバーの名前+さん・残り人数)", () => {
    const lines = cancelDialogLines(matched, ME_ID);
    expect(lines).toEqual([
      "チャットに「あなたが参加を取り消しました」と表示され、ゆうきさん・Mikaさんに伝わります",
      "あなたはこのチャットに書き込めなくなります",
      "残りの2人で会うかどうかは、2人が決めます",
    ]);
  });
});
