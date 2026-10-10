import { describe, expect, it } from "vitest";
import {
  buildCreateRequest,
  conditionTags,
  depositedAgoText,
  effectiveParticipants,
  expiryLine,
  formatBudget,
  formatCategory,
  formatCategoryPrimary,
  formatLocation,
  formatParticipants,
  formatTime,
  jstParts,
  parseBudgetText,
  parseCategoryText,
  parseParticipantsText,
  parseTimeText,
} from "../src/intent/format.js";

// docs例文の意図(03 §3・05 §5)から想定されるstructured_intent相当
// now = JST 2026-10-10(土) 12:00
const NOW = "2026-10-10T12:00:00+09:00";

describe("formatTime(v2様式「今日 10/10（土）20:00〜」)", () => {
  it("当日のstartは「今日 M/D（曜）H:mm〜」", () => {
    expect(formatTime("2026-10-10T20:00:00+09:00", null, NOW)).toBe(
      "今日 10/10（土）20:00〜",
    );
  });

  it("翌日のstartは「明日 M/D（曜）H:mm〜」", () => {
    expect(formatTime("2026-10-11T20:00:00+09:00", null, NOW)).toBe(
      "明日 10/11（日）20:00〜",
    );
  });

  it("翌々日以降は日付の語なし「M/D（曜）H:mm〜」", () => {
    expect(formatTime("2026-10-13T19:30:00+09:00", null, NOW)).toBe(
      "10/13（火）19:30〜",
    );
  });

  it("endがあれば「H:mm〜H:mm」", () => {
    expect(
      formatTime("2026-10-10T20:00:00+09:00", "2026-10-10T23:00:00+09:00", NOW),
    ).toBe("今日 10/10（土）20:00〜23:00");
  });

  it("nullは「指定なし」", () => {
    expect(formatTime(null, null, NOW)).toBe("指定なし");
  });

  it("UTC入力でもJST暦で表示する(日付境界)", () => {
    // 2026-10-10T16:00Z = JST 11日 01:00 → 「明日 10/11（日）01:00〜」
    expect(formatTime("2026-10-10T16:00:00Z", null, NOW)).toBe(
      "明日 10/11（日）01:00〜",
    );
  });
});

describe("行の表示文言", () => {
  it("場所", () => {
    expect(formatLocation({ name: "天文館" })).toBe("天文館");
    expect(formatLocation({ name: null })).toBe("指定なし");
  });

  it("人数: min==maxは「N人」・違えば「M〜N人」・nullは「指定なし」", () => {
    expect(formatParticipants({ min: 2, max: 2 })).toBe("2人");
    expect(formatParticipants({ min: 2, max: 4 })).toBe("2〜4人");
    expect(formatParticipants({ min: null, max: 3 })).toBe("3人");
    expect(formatParticipants({ min: null, max: null })).toBe("指定なし");
  });

  it("予算: 「1人 5,000円まで」(v2様式)・nullは「指定なし」", () => {
    expect(formatBudget({ max: 5000 })).toBe("1人 5,000円まで");
    expect(formatBudget({ max: 10000 })).toBe("1人 10,000円まで");
    expect(formatBudget({ max: null })).toBe("指定なし");
  });

  it("目的: secondary優先・なければprimaryの日本語ラベル・nullは「指定なし」", () => {
    expect(formatCategory({ primary: "drinking", secondary: "焼肉" })).toBe(
      "焼肉",
    );
    expect(formatCategory({ primary: "drinking", secondary: null })).toBe(
      "飲み",
    );
    expect(formatCategory({ primary: "meal", secondary: null })).toBe("食事");
    expect(formatCategory({ primary: "activity", secondary: null })).toBe(
      "アクティビティ",
    );
    expect(formatCategory({ primary: null, secondary: null })).toBe(
      "指定なし",
    );
  });

  it("カード見出しの種別語(primaryのみ・01「飲み · 12分前に預けた」)", () => {
    expect(formatCategoryPrimary({ primary: "drinking", secondary: "焼肉" })).toBe("飲み");
    expect(formatCategoryPrimary({ primary: null })).toBe("—");
  });
});

describe("01待機中カードの補助書式", () => {
  it("conditionTags: 「20:00〜」「天文館」「2〜4人」「〜¥5,000」(値のない行は組まない)", () => {
    expect(
      conditionTags({
        time: { start: "2026-10-10T20:00:00+09:00", end: null },
        location: { name: "天文館", radius_m: null },
        participants: { min: 2, max: 4 },
        budget: { max: 5000 },
      }),
    ).toEqual(["20:00〜", "天文館", "2〜4人", "〜¥5,000"]);
    expect(
      conditionTags({
        time: { start: null, end: null },
        location: { name: null, radius_m: null },
        participants: { min: null, max: null },
        budget: { max: null },
      }),
    ).toEqual([]);
  });

  it("expiryLine: 当日「今日 H:mm まで有効」・先の日付「M/D まで有効」・null「期限なし」", () => {
    expect(expiryLine("2026-10-10T23:59:00+09:00", NOW)).toBe("今日 23:59 まで有効");
    expect(expiryLine("2026-10-11T05:00:00+09:00", NOW)).toBe("10/11 まで有効");
    expect(expiryLine("2026-11-30T22:00:00+09:00", NOW)).toBe("11/30 まで有効");
    expect(expiryLine(null, NOW)).toBe("期限なし");
  });

  it("depositedAgoText: 分・時間・日", () => {
    expect(depositedAgoText("2026-10-10T11:48:00+09:00", NOW)).toBe("12分前に預けた");
    expect(depositedAgoText("2026-10-10T09:00:00+09:00", NOW)).toBe("3時間前に預けた");
    expect(depositedAgoText("2026-09-02T12:00:00+09:00", NOW)).toBe("38日前に預けた");
    expect(depositedAgoText("2026-10-10T11:59:30+09:00", NOW)).toBe("たった今預けた");
  });
});

describe("02行編集のテキストパーサ(表示文言の往復)", () => {
  it("時間: 「明日 20:00」→ 翌日JSTのISO", () => {
    expect(
      parseTimeText("明日 20:00", { nowIso: NOW, fallbackIso: null }),
    ).toBe("2026-10-11T20:00:00+09:00");
  });

  it("時間: formatTimeの出力を往復できる(「今日 10/10（土）20:00〜」)", () => {
    const display = formatTime("2026-10-10T20:00:00+09:00", null, NOW);
    expect(parseTimeText(display, { nowIso: NOW, fallbackIso: null })).toBe(
      "2026-10-10T20:00:00+09:00",
    );
  });

  it("時間: 日付の語がなければfallbackの日付・「19時半」は30分", () => {
    expect(
      parseTimeText("19時半", { nowIso: NOW, fallbackIso: "2026-10-13T20:00:00+09:00" }),
    ).toBe("2026-10-13T19:30:00+09:00");
  });

  it("時間: M/D指定・時刻なしはnull(催促へ戻す)", () => {
    expect(parseTimeText("10/15", { nowIso: NOW, fallbackIso: null })).toBe(null);
    expect(parseTimeText("", { nowIso: NOW, fallbackIso: "2026-10-13T20:00:00+09:00" })).toBe(null);
  });

  it("人数: 「2〜4人」「3人」・人数の語がなければnull", () => {
    expect(parseParticipantsText("2〜4人")).toEqual({ min: 2, max: 4 });
    expect(parseParticipantsText("3人")).toEqual({ min: 3, max: 3 });
    expect(parseParticipantsText("あなたを含む")).toBe(null);
  });

  it("予算: formatBudgetの出力を往復・万換算", () => {
    expect(parseBudgetText(formatBudget({ max: 5000 }))).toEqual({ max: 5000 });
    expect(parseBudgetText("年収 1,000万円以上")).toEqual({ max: 10_000_000 });
    expect(parseBudgetText("予算なし")).toBe(null);
  });

  it("目的: 種別の語はprimaryへ・自由文はsecondaryへ", () => {
    expect(parseCategoryText("飲み", { primary: "meal", secondary: null })).toEqual({
      primary: "drinking",
      secondary: null,
    });
    expect(parseCategoryText("軽く飲む", { primary: "drinking", secondary: null })).toEqual({
      primary: "drinking",
      secondary: "軽く飲む",
    });
    expect(parseCategoryText("", { primary: "drinking", secondary: "x" })).toEqual({
      primary: null,
      secondary: null,
    });
  });
});

describe("effectiveParticipants(D-04「含まない」の+1換算)", () => {
  it("含む/nullはそのまま・含まないは+1(あなた+2〜4人=合計3〜5人)", () => {
    expect(effectiveParticipants({ min: 2, max: 4 }, true)).toEqual({ min: 2, max: 4 });
    expect(effectiveParticipants({ min: 2, max: 4 }, null)).toEqual({ min: 2, max: 4 });
    expect(effectiveParticipants({ min: 2, max: 4 }, false)).toEqual({ min: 3, max: 5 });
  });
});

describe("jstParts", () => {
  it("JST暦で分解する(曜日・dateKey)", () => {
    const parts = jstParts("2026-10-10T16:00:00Z"); // JST 10/11 01:00 日曜
    expect(parts).toMatchObject({
      year: 2026,
      month: 10,
      day: 11,
      hour: "01",
      minute: "00",
      weekday: "Sun",
      dateKey: "2026-10-11",
    });
  });
});

describe("buildCreateRequest(02で確定保存)", () => {
  const state = {
    rawText: " 今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。 ",
    structured: {
      category: { primary: "drinking", secondary: null },
      alcohol_involved: true,
      time: { start: "2026-10-10T20:00:00+09:00", end: null },
      location: { name: "天文館", radius_m: 1000 },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
    },
    softRows: [{ id: "soft-0", text: "軽く飲みたい" }],
    ngRows: [{ id: "ng-0", text: "会社関係の人は避けたい" }],
    participantsIncludesOwner: true,
  };

  it("active保存のボディ(raw_textはtrim・soft/ng配列へ集約・softExtrasを追加分として連結)", () => {
    const body = buildCreateRequest(state, {
      status: "active",
      visibility: "hidden_until_match",
      notificationLevel: "proposals_only",
      expiresAt: "2026-10-10T23:59:00+09:00",
      softExtras: ["禁煙の店"],
    });
    expect(body.raw_text).toBe(
      "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。",
    );
    expect(body.status).toBe("active");
    expect(body.structured_intent).toEqual({
      category: { primary: "drinking", secondary: null },
      alcohol_involved: true,
      time: { start: "2026-10-10T20:00:00+09:00", end: null, flexibility_minutes: null },
      location: { name: "天文館", radius_m: 1000 },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
      participants_includes_owner: true,
      visibility: "hidden_until_match",
      notification_level: "proposals_only",
      expires_at: "2026-10-10T23:59:00+09:00",
      soft_constraints: ["軽く飲みたい", "禁煙の店"],
      ng_unverifiable: ["会社関係の人は避けたい"],
      negative_constraints: [],
    });
  });

  it("draft保存でもvisibility既定値を格納する(05 §5)", () => {
    const body = buildCreateRequest(state, {
      status: "draft",
      visibility: "hidden_until_match",
      notificationLevel: "proposals_only",
      expiresAt: null,
    });
    expect(body.status).toBe("draft");
    expect(body.structured_intent.visibility).toBe("hidden_until_match");
    expect(body.structured_intent.expires_at).toBe(null);
  });

  it("人数確認「含まない」はparticipantsを+1しfalseを送る(D-04)", () => {
    const body = buildCreateRequest(
      { ...state, participantsIncludesOwner: false },
      {
        status: "active",
        visibility: "hidden_until_match",
        notificationLevel: "proposals_only",
        expiresAt: "2026-10-10T23:59:00+09:00",
      },
    );
    expect(body.structured_intent.participants).toEqual({ min: 3, max: 5 });
    expect(body.structured_intent.participants_includes_owner).toBe(false);
  });

  it("未回答(null)は+1せずparticipants_includes_ownerは既定true", () => {
    const body = buildCreateRequest(
      { ...state, participantsIncludesOwner: null },
      {
        status: "active",
        visibility: "hidden_until_match",
        notificationLevel: "proposals_only",
        expiresAt: "2026-10-10T23:59:00+09:00",
      },
    );
    expect(body.structured_intent.participants).toEqual({ min: 2, max: 4 });
    expect(body.structured_intent.participants_includes_owner).toBe(true);
  });
});
