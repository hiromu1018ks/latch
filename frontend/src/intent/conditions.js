// 条件リストのDOM構成(design §2.5・§2.7・§2.8)。
// 行構造・ラベル・アイコンはプロトタイプ固定(00 運用ル則6)。値はformat.jsの
// 純関数が組み立てる。ユーザー入力はすべてtextContentへ設定する(innerHTMLに埋めない)。
import {
  formatBudget,
  formatCategory,
  formatLocation,
  formatParticipants,
  formatTime,
} from "./format.js";
import { dateTimeLocalToIso, isoToDateTimeLocal } from "./format.js";
import { missingRequired } from "./state.js";

export const REQUIRED_NOTE_TEXT = "時間・場所・目的を指定すると預けられます";
// 02 D-04・03 §3の注意文言(schema.py WARNING_MESSAGE_NG_DOWNGRADED と同一の固定文字列。
// サーバ応答のmessageは参考にしか使わない — 確定値13)
export const NG_NOTE_TEXT = "この条件は確実には除外できません。参考条件として扱います";

export const ROW_DEFS = [
  { key: "time", label: "時間", icon: "ph-clock" },
  { key: "location", label: "場所", icon: "ph-map-pin" },
  { key: "participants", label: "人数", icon: "ph-users-three" },
  { key: "budget", label: "予算", icon: "ph-currency-jpy" },
  { key: "category", label: "目的", icon: "ph-flag" },
];

const createEditButton = (label) => {
  const button = document.createElement("button");
  button.className = "edit-button";
  button.type = "button";
  button.setAttribute("aria-label", `${label}を編集`);
  button.innerHTML = '<i class="ph ph-pencil-simple" aria-hidden="true"></i>';
  return button;
};

const buildRow = ({ label, icon, value, extraClass = "" }) => {
  const row = document.createElement("div");
  row.className = `condition-row ${extraClass}`.trim();
  row.dataset.label = label;
  const iconBox = document.createElement("div");
  iconBox.className = "condition-icon";
  iconBox.innerHTML = `<i class="ph ${icon}" aria-hidden="true"></i>`;
  const labelEl = document.createElement("span");
  labelEl.className = "condition-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "condition-value";
  valueEl.textContent = value;
  row.append(iconBox, labelEl, valueEl, createEditButton(label));
  return row;
};

export const renderConditions = ({ listEl, state, now }) => {
  const s = state.structured;
  const values = {
    time: formatTime(s.time.start, s.time.end, now),
    location: formatLocation(s.location),
    participants: formatParticipants(s.participants),
    budget: formatBudget(s.budget),
    category: formatCategory(s.category),
  };
  const missing = new Set(missingRequired(state));

  const rows = ROW_DEFS.map((def) => {
    const row = buildRow({
      label: def.label,
      icon: def.icon,
      value: values[def.key],
      extraClass: missing.has(def.label) ? "condition-missing" : "",
    });
    if (missing.has(def.label)) row.dataset.missing = "true";
    return row;
  });

  for (const soft of state.softRows) {
    const row = buildRow({ label: "その他", icon: "ph-flag", value: soft.text });
    row.dataset.rowId = soft.id;
    rows.push(row);
  }
  for (const ng of state.ngRows) {
    const row = buildRow({
      label: "NG",
      icon: "ph-warning",
      value: ng.text,
      extraClass: "ng-row",
    });
    row.dataset.rowId = ng.id;
    // 注意文言は値ではなく行の注記として直下へ置く(値テキストと分離)
    const note = document.createElement("p");
    note.className = "ng-note";
    note.textContent = NG_NOTE_TEXT;
    row.append(note);
    rows.push(row);
  }

  listEl.replaceChildren(...rows);
};

// ---------------------------------------------------------------------------
// 行単位インライン編集(タイプ別構造化入力・design §2.5)
// 通常経路の修正と422フォールバックの手動入力が同一UI(確定値9「フォームは
// フォールバック専用で主UIにしない」の実現)。

const num = (value) => (value === "" || value == null ? null : Number(value));

const buildEditForm = (innerHtml) => {
  const form = document.createElement("form");
  form.className = "condition-edit";
  form.innerHTML = `
    ${innerHtml}
    <button type="submit" aria-label="変更を保存"><i class="ph ph-check" aria-hidden="true"></i></button>
  `;
  return form;
};

// 行タイプ別の入力要素と確定時のstate書き戻し
const editors = {
  time: {
    fields: (state) => `
      <input type="datetime-local" name="start" aria-label="時間を編集"
             value="${isoToDateTimeLocal(state.structured.time.start)}" />`,
    commit: (state, form) => {
      state.structured.time.start = dateTimeLocalToIso(form.elements.start.value);
    },
  },
  location: {
    fields: (state) => `
      <input name="name" aria-label="場所を編集" required
             value="${state.structured.location.name ?? ""}" />
      <input type="number" name="radius" min="1" aria-label="半径(メートル・任意)"
             value="${state.structured.location.radius_m ?? ""}" placeholder="半径m(任意)" />`,
    commit: (state, form) => {
      state.structured.location.name = form.elements.name.value.trim() || null;
      state.structured.location.radius_m = num(form.elements.radius.value);
    },
  },
  participants: {
    fields: (state) => `
      <input type="number" name="min" min="1" max="4" aria-label="最小人数"
             value="${state.structured.participants.min ?? ""}" placeholder="最小" />
      <input type="number" name="max" min="1" max="4" aria-label="最大人数"
             value="${state.structured.participants.max ?? ""}" placeholder="最大" />`,
    commit: (state, form) => {
      state.structured.participants.min = num(form.elements.min.value);
      state.structured.participants.max = num(form.elements.max.value);
    },
  },
  budget: {
    fields: (state) => `
      <input type="number" name="max" min="0" aria-label="予算上限(円)"
             value="${state.structured.budget.max ?? ""}" placeholder="円" />`,
    commit: (state, form) => {
      state.structured.budget.max = num(form.elements.max.value);
    },
  },
  category: {
    fields: (state) => `
      <select name="primary" aria-label="目的の種類">
        <option value="meal"${state.structured.category.primary === "meal" ? " selected" : ""}>食事</option>
        <option value="drinking"${state.structured.category.primary === "drinking" ? " selected" : ""}>飲み</option>
        <option value="activity"${state.structured.category.primary === "activity" ? " selected" : ""}>アクティビティ</option>
      </select>
      <input name="secondary" aria-label="目的の詳細(任意)"
             value="${state.structured.category.secondary ?? ""}" placeholder="詳細(任意)" />`,
    commit: (state, form) => {
      state.structured.category.primary = form.elements.primary.value || null;
      state.structured.category.secondary = form.elements.secondary.value.trim() || null;
    },
  },
};

const textEditor = {
  fields: (current) => `
    <input name="text" aria-label="条件を編集" required value="${current ?? ""}" />`,
  commit: (target, form) => {
    target.text = form.elements.text.value.trim();
  },
};

export const beginRowEdit = ({ rowEl, state, rerender }) => {
  if (rowEl.querySelector(".condition-edit")) return;
  const label = rowEl.dataset.label;
  const def = ROW_DEFS.find((d) => d.label === label);

  let editor;
  let commitTarget = null;
  if (def) {
    editor = editors[def.key];
  } else {
    // soft行・NG行: テキスト編集(soft行と同一UI・design §2.8)
    const list = label === "NG" ? state.ngRows : state.softRows;
    commitTarget = list.find((r) => r.id === rowEl.dataset.rowId) ?? null;
    editor = textEditor;
  }

  const valueEl = rowEl.querySelector(".condition-value");
  const currentText = valueEl.textContent;
  const form = buildEditForm(
    def ? editor.fields(state) : editor.fields(currentText),
  );

  const finish = (save) => {
    if (save) {
      if (def) {
        editor.commit(state, form);
      } else if (commitTarget) {
        editor.commit(commitTarget, form);
      }
    }
    rerender(); // 取消・確定とも再描画で元の行構成へ戻す
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    finish(true);
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape") finish(false);
  });

  const editButton = rowEl.querySelector(".edit-button");
  valueEl.remove();
  editButton.replaceWith(form);
  const first = form.querySelector("input, select");
  if (first) {
    first.focus();
    if (first.select) first.select();
  }
};

// 「条件を追加」(その他・曜日・移動・雰囲気 — ラベルはUI上の入力補助で
// 格納先はすべてsoft_constraints・API送信値はtextのみ: D-19補足・確定値3)
export const attachAddCondition = ({ formEl, state, rerender }) => {
  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = formEl.querySelector("input[aria-label='追加する条件']");
    const text = input.value.trim();
    if (!text) return;
    state.softRows.push({ id: `soft-${Date.now()}-${state.softRows.length}`, text });
    input.value = "";
    formEl.hidden = true; // 追加フォームを閉じ、「条件を追加」ボタンはrerender側(main.js)で再表示
    rerender();
  });
};
