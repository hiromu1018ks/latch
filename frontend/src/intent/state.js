// フォーム状態(design §2.5〜§2.7)。純オブジェクト+純関数(immerなし・都度新オブジェクト)。
// visibility・notification_level・expires_at は預け方パネル由来で保存時に組み込む
// (parse応答は07 Parser出力と同形でこれらを含まない — 05 §5)。

export const emptyStructured = () => ({
  category: { primary: null, secondary: null },
  alcohol_involved: null,
  time: { start: null, end: null },
  location: { name: null, radius_m: null },
  budget: { max: null, currency: "JPY" },
  participants: { min: null, max: null },
});

export const createFormState = () => ({
  rawText: "",
  structured: emptyStructured(),
  softRows: [], // 「条件を追加」(その他・曜日・移動・雰囲気)とparse由来soft(D-19補足)
  ngRows: [], // 判定不能NG(02 D-04・03 §3)
  parseStatus: "idle", // idle | in-flight | ok | fallback | error
});

export const applyParseResult = (state, structuredIntent) => ({
  ...state,
  structured: {
    category: { ...structuredIntent.category },
    alcohol_involved: structuredIntent.alcohol_involved,
    time: {
      start: structuredIntent.time?.start ?? null,
      end: structuredIntent.time?.end ?? null,
    },
    location: { ...structuredIntent.location },
    budget: {
      max: structuredIntent.budget?.max ?? null,
      currency: structuredIntent.budget?.currency ?? "JPY",
    },
    participants: { ...structuredIntent.participants },
  },
  softRows: (structuredIntent.soft_constraints ?? []).map((text, i) => ({
    id: `soft-${i}`,
    text,
  })),
  ngRows: (structuredIntent.ng_unverifiable ?? []).map((text, i) => ({
    id: `ng-${i}`,
    text,
  })),
  parseStatus: "ok",
});

export const applyParseFallback = (state) => ({
  ...state,
  structured: emptyStructured(),
  softRows: [],
  ngRows: [],
  parseStatus: "fallback",
});

export const missingRequired = (state) => {
  const missing = [];
  if (!state.structured.category?.primary) missing.push("目的");
  if (!state.structured.time?.start) missing.push("時間");
  if (!state.structured.location?.name) missing.push("場所");
  return missing;
};

// 預ける=テキスト非空+必須3充足(03 §3・design §2.7)
export const canSubmit = (state) =>
  state.rawText.trim().length > 0 && missingRequired(state).length === 0;

// 下書き=テキスト非空のみ(draftはraw_textのみ必須・05 §5)
export const canDraft = (state) => state.rawText.trim().length > 0;
