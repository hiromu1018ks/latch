// 02「読み解き確認」の条件行構成(UI v2 Phase 2・プロトタイプ02が正源)。
// 行=40pxアイコン枠+行番号(mono・acc)+ラベル+値(21px 700)+サブ+行末編集。
// 行編集はテキスト1行input+保存/キャンセル(プロトタイプ02どおり)で、
// 確定時にformat.jsのパーサで構造値へ書き戻す。ユーザー入力はすべて
// textContentへ設定する(innerHTMLに埋めない)。
import {
  MISSING_TEXT,
  formatBudget,
  formatCategory,
  formatLocation,
  formatParticipants,
  formatTime,
  jstParts,
  parseBudgetText,
  parseCategoryText,
  parseParticipantsText,
  parseTimeText,
} from "./format.js";
import { missingRequired } from "./state.js";
import { icon } from "../ui/icons.js";

export const REQUIRED_NOTE_TEXT = "時間・場所・目的を指定すると預けられます";
// 02 D-04・03 §3の注意文言(schema.py WARNING_MESSAGE_NG_DOWNGRADED と同一の固定文字列。
// サーバ応答のmessageは参考にしか使わない — 確定値13)
export const NG_NOTE_TEXT = "この条件は確実には除外できません。参考条件として扱います";

// アイコンはプロトタイプ02「読み取った条件」の5行(時間=clock・場所=pin・
// 人数=people・予算=yen・目的=グラス)。noはプロトタイプ02の行番号(01〜05)
export const ROW_DEFS = [
  { key: "time", label: "時間", icon: "clock", no: "01" },
  { key: "location", label: "場所", icon: "pin", no: "02" },
  { key: "participants", label: "人数", icon: "people", no: "03" },
  { key: "budget", label: "予算", icon: "yen", no: "04" },
  { key: "category", label: "目的", icon: "glass", no: "05" },
];

// 02「足せる条件(任意)」のトグルchips(プロトタイプ02の固定3値 → soft_constraints)
export const EXTRAS = [
  { id: "nosmoke", label: "禁煙の店" },
  { id: "short", label: "2時間くらいで解散" },
  { id: "fresh", label: "全員はじめまして" },
];

// 目的ごとのサブ説明(飲み=プロトタイプ02の文言。ほか2値は本実装の補完)
const CATEGORY_SUB = {
  drinking: "カジュアルな一杯 · 長居は前提にしない",
  meal: "食事をともにする時間",
  activity: "一緒に楽しむ時間",
};

// --- 01 composerの読み取りchips(値のある行のみ) ----------------------------
export const readSlots = (state, nowIso) => {
  const s = state.structured;
  const values = {
    time: formatTime(s.time.start, s.time.end, nowIso),
    location: formatLocation(s.location),
    participants: formatParticipants(s.participants),
    budget: formatBudget(s.budget),
    category: formatCategory(s.category),
  };
  return ROW_DEFS.filter((def) => values[def.key] !== MISSING_TEXT).map((def) => ({
    key: def.key,
    label: def.label,
    value: values[def.key],
  }));
};

// --- 02の5行モデル(値・サブ・催促。人数はsegの回答で変わる) ----------------
export const rowModels = (state, nowIso) => {
  const s = state.structured;
  const inc = state.participantsIncludesOwner;
  const peopleBase = formatParticipants(s.participants);
  const peopleValue =
    peopleBase === MISSING_TEXT
      ? MISSING_TEXT
      : inc === true
        ? `${peopleBase}（あなたを含む）`
        : inc === false
          ? `あなた＋${peopleBase}`
          : peopleBase;
  const peopleSub =
    inc == null
      ? "あなたを含むかどうかを確認してください"
      : peopleBase === MISSING_TEXT
        ? "人数の指定はありません"
        : inc
          ? `相手は${rangeText(s.participants.min - 1, s.participants.max - 1)}`
          : `合計${rangeText(s.participants.min + 1, s.participants.max + 1)}`;

  const values = {
    time: formatTime(s.time.start, s.time.end, nowIso),
    location: formatLocation(s.location),
    participants: peopleValue,
    budget: formatBudget(s.budget),
    category: formatCategory(s.category),
  };
  const subs = {
    time: s.time.start
      ? s.time.end
        ? `終わりは ${jstParts(s.time.end).hour}:${jstParts(s.time.end).minute}`
        : "終わりの指定なし · 有効期限まで受け付け"
      : null,
    location: s.location.name ? "歩いて集まれる範囲" : null,
    participants: peopleSub,
    budget: s.budget.max != null ? "1軒目の目安として扱います" : null,
    category: s.category.primary
      ? (CATEGORY_SUB[s.category.primary] ?? null)
      : null,
  };
  const missing = new Set(missingRequired(state));
  return ROW_DEFS.map((def) => ({
    ...def,
    value: values[def.key],
    sub: subs[def.key],
    missing: missing.has(def.label),
  }));
};

const rangeText = (min, max) => {
  const fmt = (n) => (n == null ? "?" : `${n}人`);
  if (min != null && max != null && min !== max) return `${min}〜${max}人`;
  return fmt(min ?? max);
};

// --- 02の行DOM(view) --------------------------------------------------------
export const renderRows = ({ listEl, state, nowIso }) => {
  const rows = rowModels(state, nowIso).map((row) => {
    const el = document.createElement("div");
    el.className = "slot-row";
    if (row.missing) el.dataset.missing = "true";
    el.dataset.key = row.key;
    el.dataset.label = row.label;

    const iconBox = document.createElement("span");
    iconBox.className = "slot-ic";
    iconBox.append(icon(row.icon, 19));

    const body = document.createElement("div");
    body.className = "slot-body";
    const line = document.createElement("span");
    line.className = "slot-line";
    const no = document.createElement("span");
    no.className = "slot-no";
    no.textContent = row.no;
    line.append(no, row.label);
    body.append(line);

    const value = document.createElement("span");
    value.className = "slot-value";
    value.textContent = row.value;
    body.append(value);
    if (row.sub) {
      const sub = document.createElement("span");
      sub.className = "slot-sub";
      sub.textContent = row.sub;
      body.append(sub);
    }

    if (row.key === "participants") body.append(peopleFieldset(state));

    const editButton = createEditButton(row.label);
    el.append(iconBox, body, editButton);
    return el;
  });

  const ng = ngNoteBox(state);
  if (ng) rows.push(ng);
  listEl.replaceChildren(...rows);
};

// 人数確認seg(D-04・プロトタイプ02のfieldset)。未回答=要確認(acc)
const peopleFieldset = (state) => {
  const inc = state.participantsIncludesOwner;
  const answered = inc != null;
  const base = formatParticipants(state.structured.participants);

  const box = document.createElement("fieldset");
  box.className = answered ? "people-box ok" : "people-box need";

  const legend = document.createElement("legend");
  legend.className = "vh";
  legend.textContent = "あなたを人数に含めるか";
  box.append(legend);

  const question = document.createElement("p");
  question.className = "people-q";
  const tag = document.createElement("span");
  tag.className = "people-tag";
  tag.textContent = answered ? "確認済み" : "要確認";
  question.append(tag, ` 「${base === MISSING_TEXT ? "人数" : base}」に、あなたは含まれますか？`);
  box.append(question);

  const seg = document.createElement("div");
  seg.className = "seg";
  for (const [value, label] of [
    ["in", "含む"],
    ["out", "含まない"],
  ]) {
    const labelEl = document.createElement("label");
    const input = document.createElement("input");
    input.className = "vh";
    input.type = "radio";
    input.name = "includes-owner";
    input.value = value;
    input.checked = (value === "in" && inc === true) || (value === "out" && inc === false);
    input.dataset.action = "pick-includes-owner";
    if (input.checked) labelEl.classList.add("on");
    labelEl.append(input, label);
    seg.append(labelEl);
  }
  box.append(seg);
  return box;
};

// 判定不能NG(02 D-04) — 02の5行の下にstb情報箱で出す(送信内容から取り零さない)
const ngNoteBox = (state) => {
  if (state.ngRows.length === 0) return null;
  const box = document.createElement("div");
  box.className = "ng-box";
  for (const row of state.ngRows) {
    const line = document.createElement("p");
    line.textContent = `「${row.text}」— ${NG_NOTE_TEXT}`;
    box.append(line);
  }
  return box;
};

const createEditButton = (label) => {
  const button = document.createElement("button");
  button.className = "edit-button";
  button.type = "button";
  button.dataset.action = "edit-row";
  button.setAttribute("aria-label", `${label}を編集`);
  button.setAttribute("aria-expanded", "false");
  button.append(icon("pencil", 17));
  return button;
};

// --- 行編集(テキスト1行input・プロトタイプ02) ------------------------------

// 行ごとの編集の種(シード値と、確定時のstate書き戻し)
const editors = {
  time: {
    seed: (state, nowIso) => {
      const v = formatTime(state.structured.time.start, state.structured.time.end, nowIso);
      return v === MISSING_TEXT ? "" : v;
    },
    commit: (state, form, ctx) => {
      state.structured.time.start = parseTimeText(form.elements.value.value, ctx);
      state.structured.time.end = null;
    },
  },
  location: {
    seed: (state) => state.structured.location.name ?? "",
    commit: (state, form) => {
      state.structured.location.name = form.elements.value.value.trim() || null;
    },
  },
  participants: {
    seed: (state) => {
      const v = formatParticipants(state.structured.participants);
      return v === MISSING_TEXT ? "" : v;
    },
    commit: (state, form) => {
      const text = form.elements.value.value.trim();
      const parsed = text ? parseParticipantsText(text) : { min: null, max: null };
      if (text && !parsed) return; // 読み取れない入力は現在値を保持(再描画で戻る)
      const before = JSON.stringify(state.structured.participants);
      state.structured.participants = parsed;
      if (JSON.stringify(parsed) !== before) {
        state.participantsIncludesOwner = null; // 人数が変わったらsegは要確認へ戻す
      }
    },
  },
  budget: {
    seed: (state) => {
      const v = formatBudget(state.structured.budget);
      return v === MISSING_TEXT ? "" : v;
    },
    commit: (state, form) => {
      const text = form.elements.value.value.trim();
      const parsed = text ? parseBudgetText(text) : { max: null };
      if (text && !parsed) return;
      state.structured.budget.max = parsed.max;
    },
  },
  category: {
    seed: (state) => {
      const v = formatCategory(state.structured.category);
      return v === MISSING_TEXT ? "" : v;
    },
    commit: (state, form) => {
      const parsed = parseCategoryText(form.elements.value.value, state.structured.category);
      state.structured.category = parsed;
    },
  },
};

export const beginRowEdit = ({ rowEl, state, nowIso, rerender }) => {
  if (rowEl.querySelector(".slot-edit")) return;
  const def = ROW_DEFS.find((d) => d.key === rowEl.dataset.key);
  if (!def) return;
  const editor = editors[def.key];

  const form = document.createElement("form");
  form.className = "slot-edit";
  const label = document.createElement("label");
  label.className = "vh";
  label.htmlFor = `edit-${def.key}`;
  label.textContent = `${def.label}を編集`;
  const input = document.createElement("input");
  input.className = "field";
  input.type = "text";
  input.id = `edit-${def.key}`;
  input.name = "value";
  input.autocomplete = "off";
  input.value = editor.seed(state, nowIso);
  const note = document.createElement("span");
  note.className = "slot-edit-note";
  note.textContent = "保存すると、この条件で照らし合わせ直します";
  const actions = document.createElement("div");
  actions.className = "slot-edit-actions";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "保存";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.dataset.action = "cancel-edit";
  cancel.textContent = "キャンセル";
  actions.append(save, cancel);
  form.append(label, input, note, actions);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    editor.commit(state, form, { nowIso, fallbackIso: state.structured.time.start });
    rerender();
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape") rerender(); // 取り消し(state不変)
  });
  cancel.addEventListener("click", () => rerender());

  const button = rowEl.querySelector(".edit-button");
  button.setAttribute("aria-expanded", "true");
  button.classList.add("editing");
  rowEl.querySelector(".slot-value")?.remove();
  rowEl.querySelector(".slot-sub")?.remove();
  rowEl.querySelector(".people-box")?.remove();
  rowEl.querySelector(".slot-body").append(form);
  input.focus();
  input.select();
};

// --- 02「足せる条件」トグルchips ---------------------------------------------
export const renderExtras = ({ wrapEl, picked, onToggle }) => {
  const buttons = EXTRAS.map((extra) => {
    const on = picked.includes(extra.label);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "extra-chip ghost";
    button.dataset.action = "toggle-extra";
    button.dataset.label = extra.label;
    button.setAttribute("aria-pressed", String(on));
    button.append(icon(on ? "check" : "plus", 15), extra.label);
    button.addEventListener("click", () => onToggle(extra.label));
    return button;
  });
  wrapEl.replaceChildren(...buttons);
};
