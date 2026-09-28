// structured_intent → 表示文言・APIボディ組立の純関数(design §2.5・§2.9)。
// DOM非依存・日付ライブラリ非依存(Intl のみ)。意図文言をログに出さない(01 §21)。

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

// 預け方パネルのselect表示文言 → API値(03 §3・05 §5)
export const PRIVACY_VALUES = {
  条件一致までは非公開: "hidden_until_match",
  候補にだけ概要を表示: "summary_only",
};
export const NOTIFICATION_VALUES = {
  一致したときだけ: "proposals_only",
  近い候補も知らせる: "nearby_also",
  通知しない: "muted",
};

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

export const formatTime = (startIso, endIso, nowIso) => {
  if (!startIso) return "指定なし";
  const start = jstParts(startIso);
  const diff = daysDiff(startIso, nowIso);
  const day =
    diff === 0
      ? "今日"
      : diff === 1
        ? "明日"
        : `${start.month}月${start.day}日(${WEEKDAY_JA[start.weekday] ?? ""})`;
  const from = `${start.hour}:${start.minute}`;
  const range = endIso
    ? `〜${jstParts(endIso).hour}:${jstParts(endIso).minute}`
    : "以降";
  return `${day} ${from}${range}`;
};

export const formatLocation = (location) =>
  location?.name ? location.name : "指定なし";

export const formatParticipants = (p) => {
  const min = p?.min ?? null;
  const max = p?.max ?? null;
  if (min === null && max === null) return "指定なし";
  if (min !== null && max !== null && min !== max) return `${min}〜${max}人`;
  return `${min ?? max}人`;
};

export const formatBudget = (budget) =>
  budget?.max != null
    ? `ひとり${Number(budget.max).toLocaleString("ja-JP")}円まで`
    : "指定なし";

export const formatCategory = (category) => {
  if (category?.secondary) return category.secondary;
  if (category?.primary) return CATEGORY_LABEL[category.primary] ?? category.primary;
  return "指定なし";
};

export const isoToDateTimeLocal = (iso) => {
  if (!iso) return "";
  const p = jstParts(iso);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${p.hour}:${p.minute}`;
};

// datetime-local の値はJSTと解釈し+09:00付きISOへ(design §2.5・HTML標準入力のみで確定変換)
export const dateTimeLocalToIso = (value) =>
  value ? `${value}:00+09:00` : null;

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
    participants: { ...state.structured.participants },
    visibility: opts.visibility,
    notification_level: opts.notificationLevel,
    expires_at: opts.expiresAt,
    soft_constraints: state.softRows.map((row) => row.text),
    ng_unverifiable: state.ngRows.map((row) => row.text),
    negative_constraints: [], // FR-42: 常に空配列
  },
});
