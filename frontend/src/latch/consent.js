// 同意フロー(M4 ws-5 計画Task 9・08 D-14)。オンボーディング画面(#/onboarding)
// と設定画面第3セクション「LLM送信の同意」を制御する。文言はtexts.jsの
// CONSENT_*定数(2026-10-03ユーザー承認済み・単一ソース)。APIは
// POST/GET /v1/users/me/consent(冪等・後勝ち)。通知許可(D-18・OS通知)とは
// 根拠も効果も別物のため、タイトルで明示分離する(design §2.4)。
import {
  CONSENT_AGREE_LABEL,
  CONSENT_CHECK_LABEL,
  CONSENT_CONDITION_TEXT,
  CONSENT_DESTINATIONS_TEXT,
  CONSENT_LEAD_TEXT,
  CONSENT_NO_TRAINING_TEXT,
  CONSENT_REQUIRED_TOAST_TEXT,
  CONSENT_SCOPE_TEXT,
  CONSENT_STATE_AGREED_TEXT,
  CONSENT_STATE_NOT_AGREED_TEXT,
  CONSENT_TITLE,
  CONSENT_TOAST_TEXT,
} from "./texts.js";
import { escapeHtml, noticeTimeText } from "./view.js";

const buildBody = () => `
  <p class="consent-lead">${escapeHtml(CONSENT_LEAD_TEXT)}</p>
  <ul class="consent-points">
    <li>${escapeHtml(CONSENT_DESTINATIONS_TEXT)}</li>
    <li>${escapeHtml(CONSENT_SCOPE_TEXT)}</li>
    <li>${escapeHtml(CONSENT_NO_TRAINING_TEXT)}</li>
  </ul>
  <p class="consent-condition">${escapeHtml(CONSENT_CONDITION_TEXT)}</p>`;

const stateLine = (consentedAt) =>
  consentedAt == null
    ? CONSENT_STATE_NOT_AGREED_TEXT
    : `${CONSENT_STATE_AGREED_TEXT}${noticeTimeText(consentedAt)}`;

export const createConsent = ({ client, chrome, el }) => {
  // el: { onboarding: $("#consentOnboarding"), settings: $("#consentControl") }
  let state = { consented_at: null };

  const renderInto = (mount, { interactive }) => {
    const agreed = state.consented_at != null;
    // v2部品(card+cta)で構成(UI v2 Phase 5)。文言は2026-10-03承認済みのまま
    mount.innerHTML = `
      <section class="card consent-card">
        <p class="consent-title">${escapeHtml(CONSENT_TITLE)}</p>
        ${buildBody()}
        <p class="consent-state"${agreed ? ' data-agreed="true"' : ""} data-role="consent-state">${escapeHtml(stateLine(state.consented_at))}</p>
        ${
          agreed || !interactive
            ? ""
            : `<label class="consent-check"><input type="checkbox" data-role="consent-check" /> ${escapeHtml(CONSENT_CHECK_LABEL)}</label>
               <button type="button" class="cta consent-agree" data-role="consent-agree" disabled>${escapeHtml(CONSENT_AGREE_LABEL)}</button>`
        }
      </section>`;
    if (agreed || !interactive) return;
    const check = mount.querySelector("[data-role=consent-check]");
    const button = mount.querySelector("[data-role=consent-agree]");
    check.addEventListener("change", () => {
      button.disabled = !check.checked; // 未チェック時は無効(design §2.4②)
    });
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        state = await client.call("POST", "/v1/users/me/consent"); // design §2.4③
        chrome.showToast(CONSENT_TOAST_TEXT);
        renderInto(el.onboarding, { interactive: true });
        location.hash = "#/"; // 同意済み → ホームへ戻す
      } catch {
        button.disabled = false; // 通信失敗時は再試行可能なまま(状態維持)
      }
    });
  };

  const fetchState = async () => {
    state = await client.call("GET", "/v1/users/me/consent"); // design §2.4④
    return state;
  };

  return {
    async load() {
      // 設定画面第3セクションの再確認表示(現在状態・同意日時・文言全文)
      await fetchState();
      renderInto(el.settings, { interactive: false });
    },
    async show() {
      // オンボーディング画面(チェックボックス+ボタン付き)
      await fetchState();
      renderInto(el.onboarding, { interactive: true });
    },
    async ensureGuidance() {
      // 誘導経路(a): 未同意ならオンボーディングへ
      const current = await fetchState();
      if (current.consented_at == null) location.hash = "#/onboarding";
    },
    notifyRequired() {
      // 誘導経路(b): 422 CONSENT_REQUIRED検出時(main.jsの横断検出から呼ばれる)
      chrome.showToast(CONSENT_REQUIRED_TOAST_TEXT);
      location.hash = "#/onboarding";
    },
  };
};
