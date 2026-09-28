import { describe, expect, it } from "vitest";
import {
  buildCreateRequest,
  dateTimeLocalToIso,
  formatBudget,
  formatCategory,
  formatLocation,
  formatParticipants,
  formatTime,
  isoToDateTimeLocal,
  jstParts,
} from "../src/intent/format.js";

// docs例文の意図(03 §3・05 §5)から想定されるstructured_intent相当
const NOW = "2026-09-27T12:00:00+09:00"; // JST 日曜 12:00

describe("formatTime", () => {
  it("当日のstartは「今日 H:mm以降」", () => {
    expect(formatTime("2026-09-27T20:00:00+09:00", null, NOW)).toBe(
      "今日 20:00以降",
    );
  });

  it("翌日のstartは「明日 H:mm以降」", () => {
    expect(formatTime("2026-09-28T20:00:00+09:00", null, NOW)).toBe(
      "明日 20:00以降",
    );
  });

  it("翌々日以降は「M月D日(曜) H:mm以降」", () => {
    expect(formatTime("2026-09-29T19:30:00+09:00", null, NOW)).toBe(
      "9月29日(火) 19:30以降",
    );
  });

  it("endがあれば「H:mm〜H:mm」", () => {
    expect(
      formatTime("2026-09-27T20:00:00+09:00", "2026-09-27T23:00:00+09:00", NOW),
    ).toBe("今日 20:00〜23:00");
  });

  it("nullは「指定なし」", () => {
    expect(formatTime(null, null, NOW)).toBe("指定なし");
  });

  it("UTC入力でもJST暦で表示する(日付境界・Review Focus 7)", () => {
    // 2026-09-27T16:00Z = JST 28日 01:00 → 「明日 01:00以降」
    expect(formatTime("2026-09-27T16:00:00Z", null, NOW)).toBe(
      "明日 01:00以降",
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

  it("予算: カンマ区切り・nullは「指定なし」", () => {
    expect(formatBudget({ max: 5000 })).toBe("ひとり5,000円まで");
    expect(formatBudget({ max: 10000 })).toBe("ひとり10,000円まで");
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
});

describe("datetime-local ↔ ISO変換(JST固定)", () => {
  it("ISO→datetime-local(JST)", () => {
    expect(isoToDateTimeLocal("2026-09-28T20:00:00+09:00")).toBe(
      "2026-09-28T20:00",
    );
    expect(isoToDateTimeLocal("2026-09-27T16:00:00Z")).toBe("2026-09-28T01:00");
    expect(isoToDateTimeLocal(null)).toBe("");
  });

  it("datetime-local→tz-aware ISO(+09:00)", () => {
    expect(dateTimeLocalToIso("2026-09-28T20:00")).toBe(
      "2026-09-28T20:00:00+09:00",
    );
    expect(dateTimeLocalToIso("")).toBe(null);
  });

  it("往復変換でJST表記が保たれる(Review Focus 7)", () => {
    const iso = "2026-09-27T20:00:00+09:00";
    expect(dateTimeLocalToIso(isoToDateTimeLocal(iso))).toBe(iso);
  });
});

describe("jstParts", () => {
  it("JST暦で分解する(曜日・dateKey)", () => {
    const parts = jstParts("2026-09-27T16:00:00Z"); // JST 9/28 01:00 月曜
    expect(parts).toMatchObject({
      year: 2026,
      month: 9,
      day: 28,
      hour: "01",
      minute: "00",
      weekday: "Mon",
      dateKey: "2026-09-28",
    });
  });
});

describe("buildCreateRequest", () => {
  const state = {
    rawText: " 今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。 ",
    structured: {
      category: { primary: "drinking", secondary: null },
      alcohol_involved: true,
      time: { start: "2026-09-27T20:00:00+09:00", end: null },
      location: { name: "天文館", radius_m: 1000 },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
    },
    softRows: [
      { id: "soft-0", text: "軽く飲みたい" },
      { id: "soft-1", text: "静かなお店" },
    ],
    ngRows: [{ id: "ng-0", text: "会社関係の人は避けたい" }],
  };

  it("active保存のボディ(raw_textはtrim・soft/ng配列へ集約)", () => {
    const body = buildCreateRequest(state, {
      status: "active",
      visibility: "hidden_until_match",
      notificationLevel: "proposals_only",
      expiresAt: "2026-09-27T23:30:00+09:00",
    });
    expect(body.raw_text).toBe(
      "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。",
    );
    expect(body.status).toBe("active");
    expect(body.structured_intent).toEqual({
      category: { primary: "drinking", secondary: null },
      alcohol_involved: true,
      time: { start: "2026-09-27T20:00:00+09:00", end: null, flexibility_minutes: null },
      location: { name: "天文館", radius_m: 1000 },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
      visibility: "hidden_until_match",
      notification_level: "proposals_only",
      expires_at: "2026-09-27T23:30:00+09:00",
      soft_constraints: ["軽く飲みたい", "静かなお店"],
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
});
