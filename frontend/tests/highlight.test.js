import { describe, expect, it } from "vitest";
import { buildWordsSegments } from "../src/intent/highlight.js";
import { applyParseResult, createFormState } from "../src/intent/state.js";

// now = JST 2026-10-10(土) 12:00
const NOW = "2026-10-10T12:00:00+09:00";

const PARSED = {
  category: { primary: "drinking", secondary: "軽く飲む" },
  alcohol_involved: true,
  time: { start: "2026-10-10T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: [],
  negative_constraints: [],
  ng_unverifiable: [],
};

const segmentsOf = (rawText, parsed = PARSED) => {
  const state = applyParseResult({ ...createFormState(), rawText }, parsed);
  return buildWordsSegments(state.structured, state.rawText, NOW);
};

describe("buildWordsSegments(D-03: 値マッチングのハイライト)", () => {
  it("プロトタイプ02相当の原文で5条件すべてにマークと行番号が付く", () => {
    const segs = segmentsOf("今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい");
    const marks = segs.filter((s) => s.no != null);
    expect(marks.map((s) => s.no)).toEqual(["01", "02", "03", "04", "05"]);
    expect(marks.map((s) => s.text)).toEqual([
      "今日20時以降",
      "天文館",
      "2〜4人",
      "予算5,000円まで",
      "軽く飲",
    ]);
    // 全体を連結すると原文が復元される(分割が漏れていない)
    expect(segs.map((s) => s.text).join("")).toBe(
      "今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい",
    );
  });

  it("数字以外の表記(20:00)でも時刻にマッチする", () => {
    const marks = segmentsOf("今日 20:00から 天文館で").filter((s) => s.no != null);
    expect(marks.map((s) => s.no)).toEqual(["01", "02"]);
    expect(marks[0].text).toBe("今日 20:00から");
  });

  it("「今夜」も当日の語として扱う", () => {
    const marks = segmentsOf("今夜20時以降、天文館で").filter((s) => s.no != null);
    expect(marks.map((s) => s.no)).toEqual(["01", "02"]);
    expect(marks[0].text).toBe("今夜20時以降");
  });

  it("マッチしない条件はハイライトなし(画面は破綻しない)", () => {
    const marks = segmentsOf("いつかどこかで誰かと", PARSED).filter((s) => s.no != null);
    expect(marks).toEqual([]);
  });

  it("値が重なる場合は先頭位置が早い方を優先し重ならない", () => {
    // 「天文館で4人」— 場所(天文館)と人数(4人)は重ならないので両方マーク
    const marks = segmentsOf("天文館で4人", {
      ...PARSED,
      participants: { min: 4, max: 4 },
    }).filter((s) => s.no != null);
    expect(marks.map((s) => s.no)).toEqual(["02", "03"]);
  });

  it("万表記の予算にもマッチする", () => {
    const marks = segmentsOf("年収1,000万円以上なら転職を考える", {
      ...PARSED,
      category: { primary: "activity", secondary: "転職を考える" },
      budget: { max: 10_000_000, currency: "JPY" },
      location: { name: null, radius_m: null },
      participants: { min: null, max: null },
      time: { start: null, end: null },
    }).filter((s) => s.no != null);
    expect(marks.map((s) => s.no)).toEqual(["04", "05"]);
    expect(marks[0].text).toBe("年収1,000万円以上");
  });
});
