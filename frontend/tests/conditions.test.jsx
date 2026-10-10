import { beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { EXTRAS, NG_NOTE_TEXT, REQUIRED_NOTE_TEXT, editors, readSlots, rowModels } from "../src/intent/conditions.js";
import { ExtraChips, SlotRows } from "../src/intent/SlotRows.jsx";
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

// SlotRowsの描画とstate更新を1つのHarnessで担う(UI v2 Phase 6から行の組立は
// Reactコンポーネントのため、旧renderRows/beginRowEditの試験意図=行の構成・
// 文言・seg・行編集の書き戻しをRTLで踏襲する)
const mountRows = (initial) => {
  cleanup(); // 同一テスト内での再mountに備えて前の描画を外す
  const holder = { state: initial };
  const Harness = () => {
    const [state, setState] = useState(holder.state);
    const [editing, setEditing] = useState(null);
    holder.state = state;
    holder.update = (updater) => {
      const next = typeof updater === "function" ? updater(holder.state) : updater;
      holder.state = next;
      setState(next);
    };
    return (
      <div id="confirmRows">
        <SlotRows
          state={state}
          editing={editing}
          onBeginEdit={(row) => setEditing(row.key)}
          onCommitEdit={(key, text) => {
            // ConfirmScreenと同じ: editors.commitをコピーstateへ適用
            holder.update((prev) => {
              const draft = structuredClone(prev);
              editors[key].commit(
                draft,
                { elements: { value: { value: text } } },
                { nowIso: NOW, fallbackIso: draft.structured.time.start },
              );
              return draft;
            });
            setEditing(null);
          }}
          onCancelEdit={() => setEditing(null)}
          onPickIncludesOwner={(value) => {
            holder.update((prev) => ({ ...prev, participantsIncludesOwner: value === "in" }));
          }}
        />
      </div>
    );
  };
  const utils = render(<Harness />);
  return { ...utils, holder };
};

const rowsEl = () => document.querySelector("#confirmRows");
const rows = () => [...rowsEl().querySelectorAll(".slot-row")];
const row = (key) => rows().find((r) => r.dataset.key === key);

const openEditor = async (key) => {
  fireEvent.click(row(key).querySelector(".edit-button"));
  await act(async () => {});
  return row(key);
};

const commitEditor = async (text) => {
  const input = rowsEl().querySelector(".slot-edit input");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(rowsEl().querySelector(".slot-edit button[type=submit]"));
  await act(async () => {});
};

beforeEach(() => {
  cleanup();
  document.body.innerHTML = "";
});

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

describe("SlotRows(02の読み取った条件5行)", () => {
  it("番号+アイコン+ラベル+値+サブの5行を構成する(文言はプロトタイプ02準拠)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    mountRows(state);
    expect(rows().map((r) => r.dataset.key)).toEqual([
      "time",
      "location",
      "participants",
      "budget",
      "category",
    ]);
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
    mountRows(createFormState());
    const missing = rowsEl().querySelectorAll('.slot-row[data-missing="true"]');
    expect([...missing].map((r) => r.dataset.key)).toEqual(["time", "location", "category"]);
    const people = row("participants");
    const seg = people.querySelector('.seg input[name="includes-owner"]');
    expect(seg).not.toBeNull();
    expect(people.querySelector(".people-tag").textContent).toBe("要確認");
  });

  it("人数確認の回答で値・サブ・タグが変わる(「含む」=そのまま/「含まない」=あなた＋)", async () => {
    mountRows(applyParseResult(createFormState(), PARSED));
    // 含む
    fireEvent.click(rowsEl().querySelector('input[value="in"]'));
    await act(async () => {});
    expect(row("participants").querySelector(".slot-value").textContent).toBe(
      "2〜4人（あなたを含む）",
    );
    expect(row("participants").querySelector(".slot-sub").textContent).toBe("相手は1〜3人");
    expect(row("participants").querySelector(".people-tag").textContent).toBe("確認済み");
    expect(row("participants").querySelector(".people-box").className).toContain("ok");
    // 含まない
    fireEvent.click(rowsEl().querySelector('input[value="out"]'));
    await act(async () => {});
    expect(row("participants").querySelector(".slot-value").textContent).toBe("あなた＋2〜4人");
    expect(row("participants").querySelector(".slot-sub").textContent).toBe("合計3〜5人");
    // 未回答
    mountRows({ ...applyParseResult(createFormState(), PARSED), participantsIncludesOwner: null });
    expect(row("participants").querySelector(".slot-value").textContent).toBe("2〜4人");
  });

  it("判定不能NGとparse由来softは5行の下に参考条件箱(注意文言は固定文字列)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    mountRows(state);
    const box = rowsEl().querySelector(".ng-box");
    expect(box.textContent).toContain("軽く飲みたい"); // parse由来soft(D-19補足)
    expect(box.textContent).toContain("会社関係の人は避けたい");
    expect(box.textContent).toContain(NG_NOTE_TEXT);
    expect(NG_NOTE_TEXT).toBe("この条件は確実には除外できません。参考条件として扱います");
    expect(box.textContent).not.toContain("undefined");
  });

  it("各行に編集ボタン(aria-label)がある", () => {
    const state = applyParseResult(createFormState(), PARSED);
    mountRows(state);
    for (const el of rows()) {
      const button = el.querySelector(".edit-button");
      expect(button.getAttribute("aria-label")).toBe(`${el.dataset.label}を編集`);
      expect(button.getAttribute("aria-expanded")).toBe("false");
    }
  });

  it("再描画でも行が重複しない(Reactの差し替え)", async () => {
    const state = applyParseResult(createFormState(), PARSED);
    const utils = mountRows(state);
    utils.rerender(<HarnessRemount state={state} />);
    await act(async () => {});
    expect(rows().length).toBe(5);
  });
});

// rerender用の最小Harness(mountRowsのHarnessと同じ構成)
const HarnessRemount = ({ state }) => (
  <div id="confirmRows">
    <SlotRows state={state} editing={null} />
  </div>
);

describe("行編集(テキスト1行input・プロトタイプ02)", () => {
  const editState = () => applyParseResult(createFormState(), PARSED);

  it("時間: 「明日 20:30」で確定するとstartが翌日JSTのISOへ書き換わる", async () => {
    const utils = mountRows(editState());
    await openEditor("time");
    await commitEditor("明日 20:30");
    expect(utils.holder.state.structured.time.start).toBe("2026-10-11T20:30:00+09:00");
  });

  it("時間: 未入力で確定するとstart=nullへ戻る(催促状態)", async () => {
    const utils = mountRows(editState());
    await openEditor("time");
    await commitEditor("");
    expect(utils.holder.state.structured.time.start).toBe(null);
  });

  it("場所: テキストをnameへ書き戻す", async () => {
    const utils = mountRows(editState());
    await openEditor("location");
    await commitEditor("天文館周辺");
    expect(utils.holder.state.structured.location.name).toBe("天文館周辺");
  });

  it("人数: 「3〜5人」を書き戻すとsegが要確認へ戻る(人数が変わったため)", async () => {
    const utils = mountRows({ ...editState(), participantsIncludesOwner: true });
    await openEditor("participants");
    await commitEditor("3〜5人");
    expect(utils.holder.state.structured.participants).toEqual({ min: 3, max: 5 });
    expect(utils.holder.state.participantsIncludesOwner).toBe(null);
  });

  it("人数: 読み取れない入力は現在値を保持(再描画で戻る)", async () => {
    const utils = mountRows(editState());
    await openEditor("participants");
    await commitEditor("たくさん");
    expect(utils.holder.state.structured.participants).toEqual({ min: 2, max: 4 });
  });

  it("予算: 「8,000円まで」をmaxへ書き戻す・空はnull", async () => {
    const utils = mountRows(editState());
    await openEditor("budget");
    await commitEditor("8,000円まで");
    expect(utils.holder.state.structured.budget.max).toBe(8000);

    await openEditor("budget");
    await commitEditor("");
    expect(utils.holder.state.structured.budget.max).toBe(null);
  });

  it("目的: 種別の語はprimary・自由文はsecondaryへ", async () => {
    const utils = mountRows(editState());
    await openEditor("category");
    await commitEditor("焼肉");
    expect(utils.holder.state.structured.category).toEqual({ primary: "drinking", secondary: "焼肉" });
  });

  it("Escapeで取り消し(stateは不変)", async () => {
    const utils = mountRows(editState());
    await openEditor("location");
    const input = rowsEl().querySelector(".slot-edit input");
    fireEvent.change(input, { target: { value: "別の場所" } });
    fireEvent.keyDown(input, { key: "Escape" });
    await act(async () => {});
    expect(utils.holder.state.structured.location.name).toBe("天文館");
  });
});

describe("ExtraChips(足せる条件トグル)", () => {
  it("固定3値のトグルchips(プロトタイプ02)・押下でon/offが反転する", async () => {
    expect(EXTRAS.map((e) => e.label)).toEqual([
      "禁煙の店",
      "2時間くらいで解散",
      "全員はじめまして",
    ]);
    const holder = { picked: [] };
    const Harness = () => {
      const [picked, setPicked] = useState(holder.picked);
      holder.update = (next) => {
        holder.picked = next;
        setPicked(next);
      };
      return <div className="extra-chips"><ExtraChips picked={picked} onToggle={(label) => holder.update(picked.includes(label) ? picked.filter((l) => l !== label) : [...picked, label])} /></div>;
    };
    render(<Harness />);
    const chip = document.querySelectorAll(".extra-chip")[0];
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(chip);
    expect(holder.picked).toEqual(["禁煙の店"]);
    expect(
      document.querySelectorAll(".extra-chip")[0].getAttribute("aria-pressed"),
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
