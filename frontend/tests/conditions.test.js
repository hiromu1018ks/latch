import { beforeEach, describe, expect, it } from "vitest";
import {
  NG_NOTE_TEXT,
  REQUIRED_NOTE_TEXT,
  attachAddCondition,
  beginRowEdit,
  renderConditions,
} from "../src/intent/conditions.js";
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

describe("beginRowEdit(タイプ別エディタ・design §2.5)", () => {
  const editState = () => applyParseResult(createFormState(), PARSED);

  // main.jsの委譲リスナーを介さずbeginRowEditを直接呼ぶ(rerenderは再描画のみ)
  const openEditor = (state, label) => {
    renderConditions({ listEl: listEl(), state, now: NOW });
    const row = listEl().querySelector(`[data-label="${label}"]`);
    beginRowEdit({
      rowEl: row,
      state,
      rerender: () => renderConditions({ listEl: listEl(), state, now: NOW }),
    });
    return listEl().querySelector(`[data-label="${label}"]`);
  };

  it("時間: datetime-localで確定するとstartがtz-aware ISOへ書き換わる", () => {
    const state = editState();
    const row = openEditor(state, "時間");
    const input = row.querySelector('input[type="datetime-local"]');
    expect(input.value).toBe("2026-09-27T20:00"); // ISO→datetime-local(JST)
    input.value = "2026-09-28T19:30";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.time.start).toBe("2026-09-28T19:30:00+09:00");
  });

  it("時間: 未入力で確定するとstart=nullへ戻る(催促状態・Review Focus 4)", () => {
    const state = editState();
    const row = openEditor(state, "時間");
    row.querySelector('input[type="datetime-local"]').value = "";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.time.start).toBe(null);
  });

  it("場所: name+半径(任意)を書き戻す", () => {
    const state = editState();
    const row = openEditor(state, "場所");
    row.querySelector('input[name="name"]').value = "天文館周辺";
    row.querySelector('input[name="radius"]').value = "2000";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.location).toEqual({ name: "天文館周辺", radius_m: 2000 });
  });

  it("人数: min/max数値(1〜4)を書き戻す・空はnull", () => {
    const state = editState();
    const row = openEditor(state, "人数");
    row.querySelector('input[name="min"]').value = "3";
    row.querySelector('input[name="max"]').value = "";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.participants).toEqual({ min: 3, max: null });
  });

  it("予算: max数値を書き戻す・空はnull", () => {
    const state = editState();
    const row = openEditor(state, "予算");
    row.querySelector('input[name="max"]').value = "8000";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.budget.max).toBe(8000);
  });

  it("目的: primaryのselect+secondaryテキスト", () => {
    const state = editState();
    const row = openEditor(state, "目的");
    row.querySelector("select").value = "meal";
    row.querySelector('input[name="secondary"]').value = "焼肉";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.category).toEqual({ primary: "meal", secondary: "焼肉" });
  });

  it("soft行: テキスト編集でsoftRowsが書き換わる", () => {
    const state = editState();
    const row = openEditor(state, "その他");
    row.querySelector("input").value = "静かなお店";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.softRows[0].text).toBe("静かなお店");
  });

  it("NG行: テキスト編集でngRowsが書き換わる(削除UIなし・design §2.8)", () => {
    const state = editState();
    const row = openEditor(state, "NG");
    row.querySelector("input").value = "取引先の人は避けたい";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.ngRows[0].text).toBe("取引先の人は避けたい");
  });

  it("Escapeで取り消し(stateは不変)", () => {
    const state = editState();
    const row = openEditor(state, "場所");
    row.querySelector('input[name="name"]').value = "別の場所";
    row.querySelector(".condition-edit").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(state.structured.location.name).toBe("天文館");
  });
});

describe("attachAddCondition(条件の追加)", () => {
  it("フォーム送信でsoftRowsへtextのみ追加される(ラベルはUI補助)", () => {
    document.body.innerHTML += `
      <form class="add-row" id="addConditionForm">
        <select id="newConditionLabel"><option>雰囲気</option></select>
        <input id="newConditionValue" aria-label="追加する条件" />
      </form>`;
    const state = applyParseResult(createFormState(), PARSED);
    const form = document.querySelector("#addConditionForm");
    attachAddCondition({ formEl: form, state, rerender: () => {} });
    document.querySelector("#newConditionValue").value = "落ち着いた席";
    form.requestSubmit();
    expect(state.softRows.map((r) => r.text)).toContain("落ち着いた席");
    expect(JSON.stringify(state.softRows)).not.toContain("雰囲気"); // ラベルは格納しない
  });
});
