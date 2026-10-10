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
  softRows: [], // parse由来soft(D-19補足)。02の「足せる条件」トグル分は保存時のsoftExtras
  ngRows: [], // 判定不能NG(02 D-04・03 §3)
  participantsIncludesOwner: null, // 02の人数確認seg(未回答=null・D-04)
  parseStatus: "idle", // idle | in-flight | ok | fallback | error
});

export const applyParseResult = (state, structuredIntent) => ({
  ...state,
  participantsIncludesOwner: null, // 再読み取りで人数は変わり得るためsegは要確認へ戻す
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

// 保存済みIntent(GET /v1/intents/{id}応答)→フォーム状態への復元
// (03の「条件を直す」=02編集モード)。structured_intentはリクエストと同形で
// 再構成されている(mapping.py to_response_structured)ため、そのまま取り込む。
// softのうち02の固定トグル分(EXTRAS)は呼び出し側がextrasPickedへ回す
export const applyIntentSnapshot = (state, intent) => {
  const s = intent.structured_intent ?? {};
  return {
    ...state,
    rawText: intent.raw_text ?? "",
    structured: {
      category: {
        primary: s.category?.primary ?? null,
        secondary: s.category?.secondary ?? null,
      },
      alcohol_involved: s.alcohol_involved ?? null,
      time: {
        start: s.time?.start ?? null,
        end: s.time?.end ?? null,
      },
      location: {
        name: s.location?.name ?? null,
        radius_m: s.location?.radius_m ?? null,
      },
      budget: { max: s.budget?.max ?? null, currency: s.budget?.currency ?? "JPY" },
      participants: {
        min: s.participants?.min ?? null,
        max: s.participants?.max ?? null,
      },
    },
    participantsIncludesOwner: s.participants_includes_owner ?? true,
    softRows: (s.soft_constraints ?? []).map((text, i) => ({
      id: `soft-${i}`,
      text,
    })),
    ngRows: (s.ng_unverifiable ?? []).map((text, i) => ({
      id: `ng-${i}`,
      text,
    })),
    parseStatus: "ok",
  };
};
