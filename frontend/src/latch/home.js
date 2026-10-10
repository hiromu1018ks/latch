// ホーム(01)の「待機中の意思」カード一覧(UI v2 Phase 2・プロトタイプ01が正源)。
// GET /v1/intents?status=active がデータ源 — カテゴリSVG+「飲み · 12分前に預けた」
// +状態pill(候補N件=acc強調/待機中=stb)+原文引用(Zen Old Mincho)+条件タグ+
// 期限行。旧latch-card(候補・成立の2セクション)はPhase 3/4の簡易一覧(D-11)へ
// 移行するためホームから外したが、ヘッダー/タブバーのバッジ(候補・成立)の
// 供給源としてGET /v1/latchesは維持する(gap分析§4-01・§6)。
// raw_textは所有者への表示のみに使い、ログへ出さない(01 §21)。
import {
  conditionTags,
  depositedAgoText,
  expiryLine,
  formatCategoryPrimary,
} from "../intent/format.js";
import { escapeHtml } from "./view.js";
import { icon } from "../ui/icons.js";

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

export const intentCardHtml = (intent, nowIso, { proposalCount = 0 } = {}) => {
  const structured = intent.structured_intent ?? {};
  const category = formatCategoryPrimary(structured.category);
  const ago = depositedAgoText(intent.created_at, nowIso);
  const tags = conditionTags(structured)
    .map((tag) => `<span class="intent-tag">${escapeHtml(tag)}</span>`)
    .join("");
  // 候補N件=acc強調カード(プロトタイプ01)。※latches一覧応答にIntentとの
  // 対応づけ(intent_ids)がないため、Phase 2では全カードを待機中(stb)で出す
  // — 候補強調の呼び出し側接続はPhase 3の持ち帰り(report参照)
  const emphasized = proposalCount > 0;
  const pill = emphasized
    ? `<span class="intent-pill acc">候補 ${proposalCount}件</span>`
    : `<span class="intent-pill stb"><span class="dot" aria-hidden="true"></span>待機中</span>`;
  const href = "#/standby"; // Phase 3で候補カード→04・ほか→03へ接続
  return `<a class="intent-card lift${emphasized ? " acc" : ""}" href="${href}">
    <div class="intent-card-head">
      <span class="intent-card-cat">${iconHtmlFor(structured.category?.primary)}<span>${escapeHtml(category)} · ${escapeHtml(ago)}</span></span>
      ${pill}
    </div>
    <p class="intent-quote">「${escapeHtml(intent.raw_text)}」</p>
    ${tags ? `<div class="intent-tags">${tags}</div>` : ""}
    <div class="intent-foot">
      <span>${escapeHtml(expiryLine(intent.expires_at, nowIso))}</span>
      <span class="intent-foot-see${emphasized ? " acc" : ""}">${
        emphasized ? `候補を見る${icon("arrowRight", 15).outerHTML}` : "静かに待機中"
      }</span>
    </div>
  </a>`;
};

const iconHtmlFor = (primary) => {
  const name = CATEGORY_ICON[primary] ?? "glass";
  return icon(name, 16).outerHTML;
};

export const createHome = ({ client, el, onCounts }) => {
  let latchItems = [];
  let latchCursor = null;
  let intentCount = 0;

  const renderIntents = (items) => {
    const nowIso = new Date().toISOString();
    el.intentCards.innerHTML = items
      .map((intent) => intentCardHtml(intent, nowIso))
      .join("");
    // 0件はヒーロー行とセクションごと隠す(プロトタイプに空状態の規定なし)
    el.standbyLine.hidden = items.length === 0;
    el.waitSection.hidden = items.length === 0;
    if (items.length > 0) {
      el.standbyLine.querySelector("[data-standby-count]").textContent =
        `${items.length}件の意思が待機中 — いまは何もしなくて大丈夫`;
    }
  };

  // latches一覧はバッジ(候補・成立)のカウント供給のみ(一覧表示はPhase 3/4)
  const renderCounts = () => {
    const { candidates, matched } = splitStatuses(latchItems);
    onCounts?.({ proposals: candidates.length, matched: matched.length });
  };

  const load = async () => {
    const data = await client.call("GET", "/v1/latches?limit=20");
    latchItems = data.items;
    latchCursor = data.next_cursor;
    renderCounts();
  };

  const loadMore = async () => {
    if (latchCursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/latches?limit=20&cursor=${encodeURIComponent(latchCursor)}`,
    );
    latchItems = latchItems.concat(data.items);
    latchCursor = data.next_cursor;
    renderCounts();
  };

  const loadIntents = async () => {
    const data = await client.call("GET", "/v1/intents?status=active");
    intentCount = data.items.length;
    renderIntents(data.items);
    onCounts?.({ standby: intentCount });
  };

  return { load, loadMore, loadIntents };
};
