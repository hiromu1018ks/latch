// LLM同意カード(M4 ws-5・08 D-14・UI v2 Phase 6でReact化)。旧consent.js
// (素のDOM配線)をコンポーネントへ載せ替えたもの。オンボーディング画面
// (interactive)と設定画面第3セクション(static)の両方で使う。文言はtexts.jsの
// CONSENT_*定数(2026-10-03ユーザー承認済み・単一ソース)。APIは
// POST/GET /v1/users/me/consent(冪等・後勝ち)。通知許可(D-18・OS通知)とは
// 根拠も効果も別物のため、タイトルで明示分離する(題は画面側のh1/h2が担うため
// カード内では出さない)。
import { useEffect, useState } from "react";
import {
  CONSENT_AGREE_LABEL,
  CONSENT_CHECK_LABEL,
  CONSENT_CONDITION_TEXT,
  CONSENT_DESTINATIONS_TEXT,
  CONSENT_LEAD_TEXT,
  CONSENT_NO_TRAINING_TEXT,
  CONSENT_SCOPE_TEXT,
  CONSENT_STATE_AGREED_TEXT,
  CONSENT_STATE_NOT_AGREED_TEXT,
  CONSENT_TOAST_TEXT,
} from "./texts.js";
import { noticeTimeText } from "./view.js";
import { useChrome } from "../ui/chrome.jsx";

const stateLine = (consentedAt) =>
  consentedAt == null
    ? CONSENT_STATE_NOT_AGREED_TEXT
    : `${CONSENT_STATE_AGREED_TEXT}${noticeTimeText(consentedAt)}`;

export const ConsentCard = ({ client, interactive = false, onAgreed }) => {
  const { showToast } = useChrome();
  const [consentedAt, setConsentedAt] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    client
      .call("GET", "/v1/users/me/consent") // design §2.4④
      .then((data) => {
        if (!alive) return;
        setConsentedAt(data.consented_at ?? null);
        setLoaded(true);
      })
      .catch(() => {}); // 通信失敗時は前回表示を維持(detailの再取得失敗と同型)
    return () => {
      alive = false;
    };
  }, [client]);

  if (!loaded) return null; // 旧show/loadも取得後に描画していた(取得までは空)

  const agreed = consentedAt != null;

  const agree = async () => {
    setBusy(true);
    try {
      const state = await client.call("POST", "/v1/users/me/consent"); // design §2.4③
      setConsentedAt(state.consented_at ?? new Date().toISOString());
      showToast(CONSENT_TOAST_TEXT);
      onAgreed?.();
    } catch {
      setBusy(false); // 通信失敗時は再試行可能なまま(状態維持)
    }
  };

  return (
    <section className="card consent-card">
      {/* v2部品(card+cta)で構成(UI v2 Phase 5)。文言は2026-10-03承認済みのまま */}
      <p className="consent-lead">{CONSENT_LEAD_TEXT}</p>
      <ul className="consent-points">
        <li>{CONSENT_DESTINATIONS_TEXT}</li>
        <li>{CONSENT_SCOPE_TEXT}</li>
        <li>{CONSENT_NO_TRAINING_TEXT}</li>
      </ul>
      <p className="consent-condition">{CONSENT_CONDITION_TEXT}</p>
      <p className="consent-state" data-role="consent-state" data-agreed={agreed ? "true" : undefined}>
        {stateLine(consentedAt)}
      </p>
      {agreed || !interactive ? null : (
        <>
          <label className="consent-check">
            <input type="checkbox" onChange={(event) => setChecked(event.target.checked)} /> {CONSENT_CHECK_LABEL}
          </label>
          {/* チェックで有効化(未チェック時は無効・design §2.4②) */}
          <button type="button" className="cta consent-agree" data-role="consent-agree" disabled={!checked || busy} onClick={agree}>
            {CONSENT_AGREE_LABEL}
          </button>
        </>
      )}
    </section>
  );
};
