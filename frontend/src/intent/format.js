// structured_intent → 表示文言・APIボディ組立の純関数(UI v2 Phase 2・02の行様式)。
// DOM非依存・日付ライブラリ非依存(Intl のみ)。意図文言をログに出さない(01 §21)。
// 表示書式はプロトタイプv2 01・02の文言が正源:
//   時間「今日 10/10（土）20:00〜」・予算「1人 5,000円まで」・
//   期限行「今日 23:59 まで有効」・カードタグ「20:00〜」「〜¥5,000」

const JST_TIME_ZONE = "Asia/Tokyo";
const WEEKDAY_JA = {
  Sun: "日",
  Mon: "月",
  Tue: "火",
  Wed: "水",
  Thu: "木",
  Fri: "金",
  Sat: "土",
};
const CATEGORY_LABEL = {
  meal: "食事",
  drinking: "飲み",
  activity: "アクティビティ",
};
export const MISSING_TEXT = "指定なし";

// D-02: 02の「公開範囲と通知」カードは選択UIを持たず、この2値で確定送信する
export const DEFAULT_VISIBILITY = "hidden_until_match";
export const DEFAULT_NOTIFICATION_LEVEL = "proposals_only";

const pad = (n) => String(n).padStart(2, "0");

export const jstParts = (iso) => {
  const date = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: JST_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value ?? "";
  const year = Number(get("year"));
  const month = Number(get("month"));
  const day = Number(get("day"));
  return {
    year,
    month,
    day,
    hour: get("hour"),
    minute: get("minute"),
    weekday: get("weekday"),
    dateKey: `${get("year")}-${get("month")}-${get("day")}`,
  };
};

const daysDiff = (aIso, bIso) => {
  const a = jstParts(aIso);
  const b = jstParts(bIso);
  const ua = Date.UTC(a.year, a.month - 1, a.day);
  const ub = Date.UTC(b.year, b.month - 1, b.day);
  return Math.round((ua - ub) / 86_400_000);
};

// 「今日 10/10（土）20:00〜」(02の01行・プロトタイプ02 BASE.timeの様式)
export const formatTime = (startIso, endIso, nowIso) => {
  if (!startIso) return MISSING_TEXT;
  const start = jstParts(startIso);
  const diff = daysDiff(startIso, nowIso);
  const date = `${start.month}/${start.day}（${WEEKDAY_JA[start.weekday] ?? ""}）`;
  const day = diff === 0 ? `今日 ${date}` : diff === 1 ? `明日 ${date}` : date;
  const from = `${start.hour}:${start.minute}`;
  const range = endIso
    ? `〜${jstParts(endIso).hour}:${jstParts(endIso).minute}`
    : "〜";
  return `${day}${from}${range}`;
};

export const formatLocation = (location) =>
  location?.name ? location.name : MISSING_TEXT;

export const formatParticipants = (p) => {
  const min = p?.min ?? null;
  const max = p?.max ?? null;
  if (min === null && max === null) return MISSING_TEXT;
  if (min !== null && max !== null && min !== max) return `${min}〜${max}人`;
  return `${min ?? max}人`;
};

// 「1人 5,000円まで」(プロトタイプ02 BASE.budgetの様式)
export const formatBudget = (budget) =>
  budget?.max != null
    ? `1人 ${Number(budget.max).toLocaleString("ja-JP")}円まで`
    : MISSING_TEXT;

export const formatCategory = (category) => {
  if (category?.secondary) return category.secondary;
  if (category?.primary) return CATEGORY_LABEL[category.primary] ?? category.primary;
  return MISSING_TEXT;
};

// 01カードの見出し用(secondaryではなく種別の語。「飲み · 12分前に預けた」)
export const formatCategoryPrimary = (category) =>
  category?.primary ? CATEGORY_LABEL[category.primary] ?? category.primary : "—";

// 01待機中カードの条件タグ(プロトタイプ01カードの「20:00〜」「天文館周辺」
// 「2〜4人」「〜¥5,000」様式。値のない行は組まない)
export const conditionTags = (structured) => {
  const tags = [];
  if (structured.time?.start) {
    const p = jstParts(structured.time.start);
    tags.push(`${p.hour}:${p.minute}〜`);
  }
  if (structured.location?.name) tags.push(structured.location.name);
  const people = formatParticipants(structured.participants);
  if (people !== MISSING_TEXT) tags.push(people);
  if (structured.budget?.max != null) {
    tags.push(`〜¥${Number(structured.budget.max).toLocaleString("ja-JP")}`);
  }
  return tags;
};

// 01カードの期限行(「今日 23:59 まで有効」「11/30 まで有効」「期限なし」—
// プロトタイプ01カードの様式: 当日のみ時刻を出し、先の日付は日付まで)
export const expiryLine = (expiresAtIso, nowIso) => {
  if (!expiresAtIso) return "期限なし";
  const p = jstParts(expiresAtIso);
  const diff = daysDiff(expiresAtIso, nowIso);
  if (diff === 0) return `今日 ${p.hour}:${p.minute} まで有効`;
  return `${p.month}/${p.day} まで有効`;
};

// 01カードの「12分前に預けた」(1分未満は「たった今」)
export const depositedAgoText = (createdIso, nowIso) => {
  const ms = new Date(nowIso).getTime() - new Date(createdIso).getTime();
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "たった今預けた";
  if (minutes < 60) return `${minutes}分前に預けた`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}時間前に預けた`;
  return `${Math.floor(hours / 24)}日前に預けた`;
};

// ---------------------------------------------------------------------------
// 02の行編集(テキスト1行input・プロトタイプ02) — 表示文言を構造値へ書き戻す
// 純関数。書式はformatTime等の出力を往復できる範囲で受理し、値がなければ
// nullへ戻す(催促状態)。判定不能な入力はnull(保存できない行は編集前の値を
// confirm.js側で保持する)

const DAY_WORDS = { 今日: 0, 今夜: 0, 明日: 1, 明後日: 2 };

// 「今日 10/10（土）20:00〜」「明日 20:00」「10/15 19:30」等 → tz-aware ISO(JST)
export const parseTimeText = (text, { nowIso, fallbackIso }) => {
  const t = String(text ?? "").replace(/（[日月火水木金土]）|\([日月火水木金土]\)/g, " ");
  const timeMatch = /(\d{1,2})[:：時](\d{2})?(半)?/.exec(t);
  if (!timeMatch) return null;
  const hour = Number(timeMatch[1]);
  const minute =
    timeMatch[2] != null ? Number(timeMatch[2]) : timeMatch[3] ? 30 : 0;
  if (hour > 23 || minute > 59) return null;

  const now = jstParts(nowIso);
  let { year, month, day } = now;
  const dayWord = Object.keys(DAY_WORDS).find((w) => t.includes(w));
  const dateMatch = /(\d{1,2})\s*\/\s*(\d{1,2})/.exec(t) ?? /(\d{1,2})月\s*(\d{1,2})日/.exec(t);
  if (dayWord != null) {
    const shifted = new Date(
      Date.UTC(now.year, now.month - 1, now.day) + DAY_WORDS[dayWord] * 86_400_000,
    );
    const iso = new Date(shifted.getTime() + 9 * 3_600_000).toISOString();
    ({ year, month, day } = jstParts(iso));
  } else if (dateMatch) {
    month = Number(dateMatch[1]);
    day = Number(dateMatch[2]);
  } else if (fallbackIso) {
    const p = jstParts(fallbackIso);
    ({ year, month, day } = p);
  }
  return `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}:00+09:00`;
};

// 「2〜4人」「3人」→ participants。人数の語がなければnull
export const parseParticipantsText = (text) => {
  const t = String(text ?? "");
  const range = /(\d+)\s*[〜~ー－-]\s*(\d+)\s*人/.exec(t);
  if (range) return { min: Number(range[1]), max: Number(range[2]) };
  const single = /(\d+)\s*人/.exec(t);
  if (single) return { min: Number(single[1]), max: Number(single[1]) };
  return null;
};

// 「1人 5,000円まで」「1,000万円以上」→ budget。万換算を含む
export const parseBudgetText = (text) => {
  const match = /([\d,，]+)\s*(万)?\s*円/.exec(String(text ?? ""));
  if (!match) return null;
  const value = Number(match[1].replace(/[，,]/g, ""));
  return { max: match[2] ? value * 10_000 : value };
};

// 種別の語ならprimaryへ、自由文ならsecondaryへ(05 §2・primaryは3値)
export const parseCategoryText = (text, category) => {
  const t = String(text ?? "").trim();
  if (!t) return { primary: null, secondary: null };
  const primary = Object.entries(CATEGORY_LABEL).find(([, label]) => label === t)?.[0];
  if (primary) return { primary, secondary: null };
  return { primary: category?.primary ?? null, secondary: t };
};

// ---------------------------------------------------------------------------
// 保存APIボディ(02で確定保存。participants_includes_owner=D-04)

// 「含まない」のときparticipantsの実効値を+1(あなた+2〜4人=合計3〜5人)
export const effectiveParticipants = (participants, includesOwner) => {
  if (includesOwner !== false) return { ...participants };
  const plus1 = (n) => (n == null ? null : n + 1);
  return { min: plus1(participants?.min), max: plus1(participants?.max) };
};

export const buildCreateRequest = (state, opts) => ({
  raw_text: state.rawText.trim(),
  status: opts.status,
  structured_intent: {
    category: { ...state.structured.category },
    alcohol_involved: state.structured.alcohol_involved,
    time: {
      start: state.structured.time.start,
      end: null, // 編集UIなし・サーバ側でtime.start+3h補完(05 §5・completion.py)
      flexibility_minutes: null,
    },
    location: { ...state.structured.location },
    budget: { ...state.structured.budget },
    participants: effectiveParticipants(
      state.structured.participants,
      state.participantsIncludesOwner,
    ),
    participants_includes_owner: state.participantsIncludesOwner ?? true, // 既定true(05 §2・D-04)
    visibility: opts.visibility,
    notification_level: opts.notificationLevel,
    expires_at: opts.expiresAt,
    soft_constraints: [
      ...state.softRows.map((row) => row.text),
      ...(opts.softExtras ?? []),
    ],
    ng_unverifiable: state.ngRows.map((row) => row.text),
    negative_constraints: [], // FR-42: 常に空配列
  },
});
