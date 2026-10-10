// ホーム(01)の「待機中の意思」周りの純関数(UI v2 Phase 2・プロトタイプ01が正源)。
// UI v2 Phase 6(React載せ替え)からカードの描画はsrc/intent/HomeScreen.jsx(React)
// が担い、このモジュールはstatus仕分け・候補との対応づけ・カテゴリアイコン名の
// 純関数群(03待機中・D-11一覧からも使われる)。
// raw_textは所有者への表示のみに使い、ログへ出さない(01 §21)。

const CANDIDATE_STATUSES = new Set(["proposed", "partial_accept"]);
const MATCHED_STATUSES = new Set(["matched", "completed"]);

export const splitStatuses = (items) => ({
  candidates: items.filter((item) => CANDIDATE_STATUSES.has(item.status)),
  matched: items.filter((item) => MATCHED_STATUSES.has(item.status)),
});

// カードのカテゴリアイコン(飲み/食事=グラス・アクティビティ=people。
// 転職・売買はschemaのカテゴリ3値に存在しないため対象外)
const CATEGORY_ICON = {
  drinking: "glass",
  meal: "glass",
  activity: "people",
};

export const categoryIconName = (primary) => CATEGORY_ICON[primary] ?? "glass";

// 候補のIntentあて(gap分析§6: latches応答にintent_idsがないため、activeが
// 1件のときだけ対応を確定できる。複数件時の対比は契約持ち帰り — phase2/3-report)
export const pairCandidateLatch = (intents, candidates) => {
  if (intents.length === 1 && candidates.length > 0) {
    return { latch: candidates[0], intentId: intents[0].id };
  }
  return null;
};
