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
