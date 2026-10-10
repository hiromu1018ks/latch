import { beforeEach, describe, expect, it } from "vitest";
import {
  EXTRAS,
  NG_NOTE_TEXT,
  REQUIRED_NOTE_TEXT,
  beginRowEdit,
  readSlots,
  renderExtras,
  renderRows,
  rowModels,
} from "../src/intent/conditions.js";
import { applyParseResult, createFormState } from "../src/intent/state.js";

// now = JST 2026-10-10(土) 12:00
const NOW = "2026-10-10T12:00:00+09:00";

const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-10-10T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: ["軽く飲みたい"],
  negative_constraints: [],
  ng_unverifiable: ["会社関係の人は避けたい"],
};

beforeEach(() => {
  document.body.innerHTML = '<div id="confirmRows"></div><div id="extraChips"></div>';
});

const rowsEl = () => document.querySelector("#confirmRows");
const extrasEl = () => document.querySelector("#extraChips");

describe("readSlots(01 composerの読み取りchips)", () => {
  it("値のある行だけ(label+value)を返す(v2様式の値)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    expect(readSlots(state, NOW)).toEqual([
      { key: "time", label: "時間", value: "今日 10/10（土）20:00〜" },
      { key: "location", label: "場所", value: "天文館" },
      { key: "participants", label: "人数", value: "2〜4人" },
      { key: "budget", label: "予算", value: "1人 5,000円まで" },
      { key: "category", label: "目的", value: "飲み" },
    ]);
  });

  it("空状態は0件(ガイド文を表示する側の条件)", () => {
    expect(readSlots(createFormState(), NOW)).toEqual([]);
  });
});

describe("renderRows(02の読み取った条件5行)", () => {
  it("番号+アイコン+ラベル+値+サブの5行を構成する(文言はプロトタイプ02準拠)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderRows({ listEl: rowsEl(), state, nowIso: NOW });
    const rows = [...rowsEl().querySelectorAll(".slot-row")];
    expect(rows.map((r) => r.dataset.key)).toEqual([
      "time",
      "location",
      "participants",
      "budget",
      "category",
    ]);
    const row = (key) => rows.find((r) => r.dataset.key === key);
    expect(row("time").querySelector(".slot-no").textContent).toBe("01");
    expect(row("time").querySelector(".slot-value").textContent).toBe(
      "今日 10/10（土）20:00〜",
    );
    expect(row("time").querySelector(".slot-sub").textContent).toBe(
      "終わりの指定なし · 有効期限まで受け付け",
    );
    expect(row("budget").querySelector(".slot-value").textContent).toBe("1人 5,000円まで");
    expect(row("budget").querySelector(".slot-sub").textContent).toBe(
      "1軒目の目安として扱います",
    );
    expect(row("time").querySelector(".slot-ic svg").dataset.icon).toBe("clock");
    expect(row("participants").querySelector(".slot-ic svg").dataset.icon).toBe("people");
  });

  it("必須3欠落行はdata-missing・人数行にはseg(D-04)がある", () => {
    renderRows({ listEl: rowsEl(), state: createFormState(), nowIso: NOW });
    const missing = [...rowsEl().querySelectorAll('.slot-row[data-missing="true"]')];
    expect(missing.map((r) => r.dataset.key)).toEqual(["time", "location", "category"]);
    const people = rowsEl().querySelector('[data-key="participants"]');
    const seg = people.querySelector('.seg input[name="includes-owner"]');
    expect(seg).not.toBeNull();
    expect(people.querySelector(".people-tag").textContent).toBe("要確認");
  });

  it("人数確認の回答で値・サブ・タグが変わる(「含む」=そのまま/「含まない」=あなた＋)", () => {
    const answered = (inc) => {
      const state = {
        ...applyParseResult(createFormState(), PARSED),
        participantsIncludesOwner: inc,
      };
      renderRows({ listEl: rowsEl(), state, nowIso: NOW });
      const people = rowsEl().querySelector('[data-key="participants"]');
      return {
        value: people.querySelector(".slot-value").textContent,
        sub: people.querySelector(".slot-sub").textContent,
        tag: people.querySelector(".people-tag").textContent,
        boxClass: people.querySelector(".people-box").className,
      };
    };
    expect(answered(true)).toMatchObject({
      value: "2〜4人（あなたを含む）",
      sub: "相手は1〜3人",
      tag: "確認済み",
    });
    expect(answered(true).boxClass).toContain("ok");
    expect(answered(false)).toMatchObject({
      value: "あなた＋2〜4人",
      sub: "合計3〜5人",
      tag: "確認済み",
    });
    expect(answered(null).value).toBe("2〜4人");
  });

  it("判定不能NGとparse由来softは5行の下に参考条件箱(注意文言は固定文字列)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderRows({ listEl: rowsEl(), state, nowIso: NOW });
    const box = rowsEl().querySelector(".ng-box");
    expect(box.textContent).toContain("軽く飲みたい"); // parse由来soft(D-19補足)
    expect(box.textContent).toContain("会社関係の人は避けたい");
    expect(box.textContent).toContain(NG_NOTE_TEXT);
    expect(NG_NOTE_TEXT).toBe("この条件は確実には除外できません。参考条件として扱います");
    expect(box.textContent).not.toContain("undefined");
  });

  it("各行に編集ボタン(aria-label)がある", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderRows({ listEl: rowsEl(), state, nowIso: NOW });
    for (const row of rowsEl().querySelectorAll(".slot-row")) {
      const button = row.querySelector(".edit-button");
      expect(button.getAttribute("aria-label")).toBe(`${row.dataset.label}を編集`);
    }
  });

  it("再描画で行が重複しない(replaceChildren)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderRows({ listEl: rowsEl(), state, nowIso: NOW });
    renderRows({ listEl: rowsEl(), state, nowIso: NOW });
    expect(rowsEl().querySelectorAll(".slot-row").length).toBe(5);
  });
});

describe("beginRowEdit(テキスト1行input・プロトタイプ02)", () => {
  const editState = () => applyParseResult(createFormState(), PARSED);

  const openEditor = (state, key) => {
    renderRows({ listEl: rowsEl(), state, nowIso: NOW });
    const row = rowsEl().querySelector(`[data-key="${key}"]`);
    beginRowEdit({
      rowEl: row,
      state,
      nowIso: NOW,
      rerender: () => renderRows({ listEl: rowsEl(), state, nowIso: NOW }),
    });
    return rowsEl().querySelector(`[data-key="${key}"]`);
  };

  it("時間: 「明日 20:30」で確定するとstartが翌日JSTのISOへ書き換わる", () => {
    const state = editState();
    const row = openEditor(state, "time");
    row.querySelector("input").value = "明日 20:30";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.time.start).toBe("2026-10-11T20:30:00+09:00");
  });

  it("時間: 未入力で確定するとstart=nullへ戻る(催促状態)", () => {
    const state = editState();
    const row = openEditor(state, "time");
    row.querySelector("input").value = "";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.time.start).toBe(null);
  });

  it("場所: テキストをnameへ書き戻す", () => {
    const state = editState();
    const row = openEditor(state, "location");
    row.querySelector("input").value = "天文館周辺";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.location.name).toBe("天文館周辺");
  });

  it("人数: 「3〜5人」を書き戻すとsegが要確認へ戻る(人数が変わったため)", () => {
    const state = { ...editState(), participantsIncludesOwner: true };
    const row = openEditor(state, "participants");
    row.querySelector("input").value = "3〜5人";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.participants).toEqual({ min: 3, max: 5 });
    expect(state.participantsIncludesOwner).toBe(null);
  });

  it("人数: 読み取れない入力は現在値を保持(再描画で戻る)", () => {
    const state = editState();
    const row = openEditor(state, "participants");
    row.querySelector("input").value = "たくさん";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.participants).toEqual({ min: 2, max: 4 });
  });

  it("予算: 「8,000円まで」をmaxへ書き戻す・空はnull", () => {
    const state = editState();
    const row = openEditor(state, "budget");
    row.querySelector("input").value = "8,000円まで";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.budget.max).toBe(8000);

    const row2 = openEditor(state, "budget");
    row2.querySelector("input").value = "";
    row2.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.budget.max).toBe(null);
  });

  it("目的: 種別の語はprimary・自由文はsecondaryへ", () => {
    const state = editState();
    const row = openEditor(state, "category");
    row.querySelector("input").value = "焼肉";
    row.querySelector(".slot-edit").requestSubmit();
    expect(state.structured.category).toEqual({ primary: "drinking", secondary: "焼肉" });
  });

  it("Escapeで取り消し(stateは不変)", () => {
    const state = editState();
    const row = openEditor(state, "location");
    row.querySelector("input").value = "別の場所";
    row.querySelector(".slot-edit").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(state.structured.location.name).toBe("天文館");
  });
});

describe("renderExtras(足せる条件トグル)", () => {
  it("固定3値のトグルchips(プロトタイプ02)・押下でon/offが反転する", () => {
    expect(EXTRAS.map((e) => e.label)).toEqual([
      "禁煙の店",
      "2時間くらいで解散",
      "全員はじめまして",
    ]);
    const picked = [];
    const rerender = () =>
      renderExtras({
        wrapEl: extrasEl(),
        picked,
        onToggle: (label) => {
          picked.splice(0, picked.length, ...(picked.includes(label) ? [] : [label]));
          rerender();
        },
      });
    rerender();
    const chip = extrasEl().querySelectorAll(".extra-chip")[0];
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    chip.click();
    expect(picked).toEqual(["禁煙の店"]);
    expect(
      extrasEl().querySelectorAll(".extra-chip")[0].getAttribute("aria-pressed"),
    ).toBe("true");
  });
});

describe("rowModels(行モデルの純関数)", () => {
  it("未回答の人数サブは催促文・必須欠落はvalue=指定なし", () => {
    const rows = rowModels(createFormState(), NOW);
    expect(rows.find((r) => r.key === "participants").sub).toBe(
      "あなたを含むかどうかを確認してください",
    );
    expect(rows.find((r) => r.key === "time").value).toBe("指定なし");
    expect(rows.find((r) => r.key === "time").missing).toBe(true);
  });
});

describe("固定文言", () => {
  it("催促注記の文言", () => {
    expect(REQUIRED_NOTE_TEXT).toBe("時間・場所・目的を指定すると預けられます");
  });
});
