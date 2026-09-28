import { beforeEach, describe, expect, it } from "vitest";
import { NG_NOTE_TEXT, REQUIRED_NOTE_TEXT, renderConditions } from "../src/intent/conditions.js";
import { applyParseResult, createFormState } from "../src/intent/state.js";

const NOW = "2026-09-27T12:00:00+09:00";

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

beforeEach(() => {
  document.body.innerHTML = '<div class="condition-list" id="conditionList"></div>';
});

const listEl = () => document.querySelector("#conditionList");

describe("renderConditions(design §2.5・§2.7・§2.8)", () => {
  it("parse結果から5行+soft行+NG行を構成する(文言はプロトタイプ固定)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    const rows = [...listEl().querySelectorAll(".condition-row")];
    expect(rows.map((r) => r.dataset.label)).toEqual([
      "時間",
      "場所",
      "人数",
      "予算",
      "目的",
      "その他",
      "NG",
    ]);
    const text = (label) =>
      rows.find((r) => r.dataset.label === label).querySelector(".condition-value").textContent;
    expect(text("時間")).toBe("今日 20:00以降");
    expect(text("場所")).toBe("天文館");
    expect(text("人数")).toBe("2〜4人");
    expect(text("予算")).toBe("ひとり5,000円まで");
    expect(text("目的")).toBe("飲み");
    expect(text("その他")).toBe("軽く飲みたい");
    expect(text("NG")).toBe("会社関係の人は避けたい");
  });

  it("必須3欠落行は催促状態(data-missing・「指定なし」)", () => {
    renderConditions({ listEl: listEl(), state: createFormState(), now: NOW });
    const missing = [...listEl().querySelectorAll('.condition-row[data-missing="true"]')];
    expect(missing.map((r) => r.dataset.label)).toEqual(["時間", "場所", "目的"]);
    for (const row of missing) {
      expect(row.querySelector(".condition-value").textContent).toBe("指定なし");
    }
  });

  it("soft行はph-flag・追加行と同型", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    const softRow = listEl().querySelector('[data-label="その他"]');
    expect(softRow.querySelector(".condition-icon i").className).toContain("ph-flag");
  });

  it("NG行はph-warning・data-label=NG・直下に注意文言(02 D-04)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    const ngRow = listEl().querySelector('[data-label="NG"]');
    expect(ngRow.querySelector(".condition-icon i").className).toContain("ph-warning");
    const note = ngRow.querySelector(".ng-note");
    expect(note.textContent).toBe(NG_NOTE_TEXT);
    expect(NG_NOTE_TEXT).toBe("この条件は確実には除外できません。参考条件として扱います");
  });

  it("各行に編集ボタン(aria-label)がある", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    for (const row of listEl().querySelectorAll(".condition-row")) {
      const button = row.querySelector(".edit-button");
      expect(button).not.toBeNull();
      expect(button.getAttribute("aria-label")).toBe(`${row.dataset.label}を編集`);
    }
  });

  it("再描画で行が重複しない(replaceChildren)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    renderConditions({ listEl: listEl(), state, now: NOW });
    expect(listEl().querySelectorAll(".condition-row").length).toBe(7);
  });
});

describe("固定文言", () => {
  it("催促注記の文言", () => {
    expect(REQUIRED_NOTE_TEXT).toBe("時間・場所・目的を指定すると預けられます");
  });
});
