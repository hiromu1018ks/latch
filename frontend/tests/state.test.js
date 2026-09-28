import { describe, expect, it } from "vitest";
import {
  applyParseFallback,
  applyParseResult,
  canDraft,
  canSubmit,
  createFormState,
  missingRequired,
} from "../src/intent/state.js";

const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-09-27T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: ["軽く飲みたい"],
  negative_constraints: [],
  ng_unverifiable: ["会社関係の人は避けたい"],
};

describe("初期状態", () => {
  it("必須3はすべて欠落・催促3行・預けられない", () => {
    const state = createFormState();
    expect(missingRequired(state)).toEqual(["目的", "時間", "場所"]);
    expect(canSubmit(state)).toBe(false);
  });

  it("テキストが空のとき下書きも不可(raw_textのみ必須)", () => {
    expect(canDraft(createFormState())).toBe(false);
  });
});

describe("applyParseResult", () => {
  it("5行分を格納しsoft/ngを行リストへ分離する", () => {
    const state = applyParseResult(createFormState(), PARSED);
    expect(state.structured.category).toEqual({ primary: "drinking", secondary: null });
    expect(state.structured.time.start).toBe("2026-09-27T20:00:00+09:00");
    expect(state.softRows).toEqual([{ id: "soft-0", text: "軽く飲みたい" }]);
    expect(state.ngRows).toEqual([{ id: "ng-0", text: "会社関係の人は避けたい" }]);
    expect(state.parseStatus).toBe("ok");
  });

  it("必須3が揃い預けられる(テキストあり・Review Focus 1の前提)", () => {
    const withText = { ...createFormState(), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const state = applyParseResult(withText, PARSED);
    expect(missingRequired(state)).toEqual([]);
    expect(canSubmit(state)).toBe(true);
  });

  it("visibility・notification_level・expires_atを状態に持たない(預け方パネルの選択値)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    expect(state.structured).not.toHaveProperty("visibility");
    expect(state.structured).not.toHaveProperty("notification_level");
    expect(state.structured).not.toHaveProperty("expires_at");
  });
});

describe("applyParseFallback(422構造化不能)", () => {
  it("5行を空に戻し催促状態にする・rawTextは保持", () => {
    const parsed = applyParseResult(
      { ...createFormState(), rawText: "今日20時以降、天文館で軽く飲みたい。" },
      PARSED,
    );
    const fallback = applyParseFallback(parsed);
    expect(fallback.rawText).toBe("今日20時以降、天文館で軽く飲みたい。");
    expect(missingRequired(fallback)).toEqual(["目的", "時間", "場所"]);
    expect(fallback.softRows).toEqual([]);
    expect(fallback.ngRows).toEqual([]);
    expect(fallback.parseStatus).toBe("fallback");
    // 必須3が揃わないままでもdraftは保存できる(Review Focus 1)
    expect(canDraft(fallback)).toBe(true);
  });
});

describe("必須3の個別欠落", () => {
  it("場所だけ欠けても預けられない", () => {
    const state = {
      ...createFormState(),
      rawText: "x",
      structured: {
        ...createFormState().structured,
        category: { primary: "drinking", secondary: null },
        time: { start: "2026-09-27T20:00:00+09:00", end: null },
      },
    };
    expect(missingRequired(state)).toEqual(["場所"]);
    expect(canSubmit(state)).toBe(false);
  });
});
