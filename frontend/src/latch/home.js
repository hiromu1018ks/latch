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

export const categoryIconName = (primary) => CATEGORY_ICON[primary] ?? "glass";

// 候補のIntentあて(gap分析§6: latches応答にintent_idsがないため、activeが
// 1件のときだけ対応を確定できる。複数件時の対比は契約持ち帰り — phase2/3-report)
export const pairCandidateLatch = (intents, candidates) => {
  if (intents.length === 1 && candidates.length > 0) {
    return { latch: candidates[0], intentId: intents[0].id };
  }
  return null;
};

export const intentCardHtml = (intent, nowIso, { proposalCount = 0, proposalHref = null } = {}) => {
  const structured = intent.structured_intent ?? {};
  const category = formatCategoryPrimary(structured.category);
  const ago = depositedAgoText(intent.created_at, nowIso);
  const tags = conditionTags(structured)
    .map((tag) => `<span class="intent-tag">${escapeHtml(tag)}</span>`)
    .join("");
  // 候補N件=acc強調カード(プロトタイプ01)。対応づけが確定できる
  // (active1件×候補あり)ときだけ04へ直結し、それ以外は03へ
  const emphasized = proposalCount > 0;
  const href = emphasized && proposalHref ? proposalHref : "#/standby";
  const pill = emphasized
    ? `<span class="intent-pill acc">候補 ${proposalCount}件</span>`
    : `<span class="intent-pill stb"><span class="dot" aria-hidden="true"></span>待機中</span>`;
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
  let intents = [];

  // カード一覧(候補との対応づけが確定できるときだけacc強調+04直結)
  const renderIntents = () => {
    const nowIso = new Date().toISOString();
    const { candidates } = splitStatuses(latchItems);
    const paired = pairCandidateLatch(intents, candidates);
    el.intentCards.innerHTML = intents
      .map((intent) => {
        const emphasized = paired != null && paired.intentId === intent.id;
        return intentCardHtml(intent, nowIso, {
          proposalCount: emphasized ? candidates.length : 0,
          proposalHref: emphasized ? `#/latches/${paired.latch.id}` : null,
        });
      })
      .join("");
    // 0件はヒーロー行とセクションごと隠す(プロトタイプに空状態の規定なし)
    el.standbyLine.hidden = intents.length === 0;
    el.waitSection.hidden = intents.length === 0;
    if (intents.length > 0) {
      el.standbyLine.querySelector("[data-standby-count]").textContent =
        `${intents.length}件の意思が待機中 — いまは何もしなくて大丈夫`;
    }
  };

  // latches一覧はバッジ(候補・成立)のカウント供給とカードの対応づけ
  const renderCounts = () => {
    const { candidates, matched } = splitStatuses(latchItems);
    onCounts?.({ proposals: candidates.length, matched: matched.length });
  };

  const load = async () => {
    const data = await client.call("GET", "/v1/latches?limit=20");
    latchItems = data.items;
    latchCursor = data.next_cursor;
    renderCounts();
    renderIntents();
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
    renderIntents();
  };

  const loadIntents = async () => {
    const data = await client.call("GET", "/v1/intents?status=active");
    intents = data.items;
    renderIntents();
    onCounts?.({ standby: intents.length });
  };

  return { load, loadMore, loadIntents };
};
