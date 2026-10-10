// 02「読み解き確認」(UI v2 Phase 6・プロトタイプ02が正源)。旧confirm.js(素の
// DOM配線)をコンポーネントへ載せ替えたもの。あなたの言葉(D-03の値マッチング
// ハイライト)・読み取った条件5行+行編集・人数確認seg(D-04)・足せる条件トグル・
// 公開範囲と通知カード+期限select(D-01・D-02)・CTA「この条件で預ける」
// (人数確認必須)→保存→#/standby。stateは01と共有(App保持のフォームstate)。
// 03の「条件を直す」(intentIdあり)では保存済みIntentをGETで復元し、CTA保存を
// PATCH /v1/intents/{id}(全置換)に切替える。下書き編集はPATCHにstatus:"active"
// を添えてactive化する(D-13)。意図文言をログに出さない(01 §21)。
import { useEffect, useRef, useState } from "react";
import { missingRequired } from "./state.js";
import { buildWordsSegments } from "./highlight.js";
import { EXTRAS } from "./conditions.js";
import { editors } from "./conditions.js";
import { ExtraChips, SlotRows } from "./SlotRows.jsx";
import { createSaveFlow } from "./save.js";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "./expiry.js";
import { applyIntentSnapshot } from "./state.js";
import { DEFAULT_NOTIFICATION_LEVEL, DEFAULT_VISIBILITY } from "./format.js";
import { Icon } from "../ui/Icon.jsx";

export const ConfirmScreen = ({ client, state, getState, setState, intentId }) => {
  const [extrasPicked, setExtrasPicked] = useState([]);
  const [busy, setBusy] = useState(false); // CTA「この条件で預ける」の処理中
  const [editing, setEditing] = useState(null); // 行編集中のkey(null=なし)
  const [expiryReady, setExpiryReady] = useState(false); // 期限selectの選択肢取得済み
  const [expiryLabel, setExpiryLabel] = useState("—");
  const [error, setError] = useState(null); // errorPlacementの戻り値
  const expirySelectRef = useRef(null);

  // 非同期フローから触る最新値
  const extrasRef = useRef(extrasPicked);
  extrasRef.current = extrasPicked;
  const intentIdRef = useRef(intentId);
  intentIdRef.current = intentId;
  const editingStatusRef = useRef(null);

  // --- あなたの言葉(原文ハイライト・D-03) ------------------------------------
  const wordsSegments = buildWordsSegments(state.structured, state.rawText, new Date().toISOString());

  // --- 公開範囲と通知(D-02: 説明3項目+期限selectのみ・選択UIなし) -------------
  const syncExpiryLabel = () => {
    if (!expirySelectRef.current) return;
    const { label } = selectedExpiry(expirySelectRef.current);
    setExpiryLabel(label ?? "—");
  };

  const refreshExpiry = async () => {
    try {
      const data = await fetchExpiryOptions(client, getState().structured.time.start);
      if (expirySelectRef.current) {
        applyExpiryOptions(expirySelectRef.current, data);
        setExpiryReady(true);
      }
    } catch {
      // 取得失敗時は既存の選択肢のまま続行(保存時は現在の選択値を送る)
    }
    syncExpiryLabel();
  };

  // --- 画面へ入る(新規: 01を経由せず来たら01へ戻す/編集: GETで復元) ------------
  useEffect(() => {
    if (intentId == null) {
      if (!getState().rawText.trim()) {
        location.hash = "#/";
        return;
      }
      setError(null);
      setEditing(null);
      refreshExpiry();
      return;
    }
    let alive = true;
    (async () => {
      let currentExpiry = null;
      try {
        const data = await client.call("GET", `/v1/intents/${intentId}`);
        if (!alive) return;
        setState(applyIntentSnapshot(getState(), data.intent));
        currentExpiry = data.intent?.expires_at ?? null;
        editingStatusRef.current = data.intent?.status ?? null; // 下書き編集はCTAでactive化(D-13)
      } catch {
        // 消済み・他者Intentは待機中へ戻す(状態の文言を出さない)
        location.hash = "#/standby";
        return;
      }
      if (!alive) return;
      // 02の固定トグル分はsoftRowsでなくextrasPickedへ(保存時に重複しない)
      const softTexts = getState().softRows.map((row) => row.text);
      setExtrasPicked(
        EXTRAS.filter((extra) => softTexts.includes(extra.label)).map((extra) => extra.label),
      );
      await refreshExpiry();
      if (!alive) return;
      // 保存済みのexpires_atが3択にあればそれを選んでおく(無い値は既定選択のまま)
      const select = expirySelectRef.current;
      const idx = [...select.options].findIndex((option) => option.value === currentExpiry);
      if (idx >= 0) {
        select.selectedIndex = idx;
        syncExpiryLabel();
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intentId]);

  // --- 行編集(editors.commitをコピーstateへ適用) ------------------------------
  const commitEdit = (key, text) => {
    setState((prev) => {
      const draft = structuredClone(prev);
      editors[key].commit(draft, { elements: { value: { value: text } } }, {
        nowIso: new Date().toISOString(),
        fallbackIso: draft.structured.time.start,
      });
      return draft;
    });
    setEditing(null);
  };

  // --- CTA「この条件で預ける」の派生値(人数確認必須・D-04) ---------------------
  const answered = state.participantsIncludesOwner != null;
  const missing = missingRequired(state);
  const unconfirmed = (answered ? 0 : 1) + missing.length;
  const summaryText =
    unconfirmed === 0
      ? "5項目 · すべて確認済み"
      : `5項目 · 確認が必要なもの ${unconfirmed}件`;
  const ctaHelpClass = unconfirmed === 0 ? "cta-help" : "cta-help strong";
  const ctaHelpText = busy
    ? "サーバーと照らし合わせています"
    : !answered
      ? "「03 人数」の確認に答えると、預けられます。"
      : missing.length > 0
        ? "時間・場所・目的を指定すると預けられます"
        : "預けたあとも、成立するまではいつでも直したり取り下げたりできます。";

  // --- 保存(02で確定保存→#/standby。D-13: 下書き導線は01側) ------------------
  // 編集モード(03「条件を直す」)ではPATCH /v1/intents/{id}(全置換・status維持)
  const saveFlowRef = useRef(null);
  if (!saveFlowRef.current) {
    saveFlowRef.current = createSaveFlow({
      client,
      getState,
      getIntentId: () => intentIdRef.current,
      getIntentStatus: () =>
        intentIdRef.current != null && editingStatusRef.current === "draft" ? "active" : null,
      getOptions: () => ({
        visibility: DEFAULT_VISIBILITY, // D-02: 既定値で確定送信
        notificationLevel: DEFAULT_NOTIFICATION_LEVEL,
        expiresAt: selectedExpiry(expirySelectRef.current).expiresAt,
        softExtras: extrasRef.current,
      }),
      onBusy: setBusy,
      onActiveSaved: () => {
        // 新規は03の着地状態・編集は通常の待機中へ(クエリで03側が2姿を切替える)
        location.hash = intentIdRef.current == null ? "#/standby?deposited=1" : "#/standby";
      },
      onDraftSaved: () => {},
      onFormError: (placement) => setError(placement),
    });
  }

  return (
    <section className="page-main page-main--confirm" id="confirmScreen">
      <div className="haze haze-confirm" aria-hidden="true"></div>

      <div className="confirm-head">
        <ol className="steps" aria-label="預けるまでの手順">
          <li className="step done">
            <span className="step-check" aria-hidden="true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7"></path></svg>
            </span>
            <span>書く<span className="vh">（完了）</span></span>
          </li>
          <li className="step now" aria-current="step">
            <span className="step-line" aria-hidden="true"></span>
            <span className="step-no" aria-hidden="true">02</span>
            <span>読み解きを確認<span className="vh">（現在の手順）</span></span>
          </li>
          <li className="step todo">
            <span className="step-line" aria-hidden="true"></span>
            <span className="step-no" aria-hidden="true">03</span>
            <span>預ける</span>
          </li>
        </ol>
        <h1 className="confirm-title">こう読み解きました。</h1>
        <p className="confirm-lead">ずれていたら、ここで直してください。確認して預けたら、あとは待つだけです。</p>
      </div>

      <div className="confirm-grid">
        <div className="confirm-col">
          <section className="card words-card" aria-labelledby="h-words">
            <div className="card-head-row">
              <h2 id="h-words" className="card-title">あなたの言葉</h2>
              <a className="rewrite-link" href="#/" data-action="rewrite">
                <Icon name="pencil" size={15} />
                書き直す
              </a>
            </div>
            <WordsText segments={wordsSegments} />
            <p className="words-note">番号は、読み取った条件の番号に対応しています</p>
          </section>

          <section className="card privacy-card" aria-labelledby="h-privacy">
            <h2 id="h-privacy" className="card-title">公開範囲と通知</h2>
            <ul className="privacy-list">
              <li className="privacy-item">
                <span className="privacy-ic">
                  <Icon name="eyeOff" size={19} />
                </span>
                <div className="privacy-body">
                  <p className="privacy-t">成立するまで、誰にも表示されません</p>
                  <p className="privacy-s">双方が「参加する」と答えたときにだけ、はじめてお互いが見えます。</p>
                </div>
              </li>
              <li className="privacy-item">
                <span className="privacy-ic">
                  <Icon name="bell" size={19} />
                </span>
                <div className="privacy-body">
                  <p className="privacy-t">通知は「LATCH候補があります」の一文だけ</p>
                  <p className="privacy-s">ロック画面に「飲みたい」という意思が出ることはありません。</p>
                </div>
              </li>
              <li className="privacy-item">
                <span className="privacy-ic">
                  <Icon name="clock" size={19} />
                </span>
                <div className="privacy-body">
                  <p className="privacy-t"><span id="expiryLabel">{expiryLabel}</span> に自動で取り下げ</p>
                  <p className="privacy-s">時間を過ぎた条件は、静かに消えます。</p>
                  <label htmlFor="expiry" className="expiry-label">取り下げる時刻
                    {/* 選択肢はexpiry.js(applyExpiryOptions)が命令的に組む —
                        Reactはselectの子を管理しない(旧実装と同じ構成) */}
                    <select
                      id="expiry"
                      className="field"
                      disabled={!expiryReady}
                      ref={expirySelectRef}
                      onChange={syncExpiryLabel}
                    ></select>
                  </label>
                </div>
              </li>
            </ul>
          </section>
        </div>

        <section className="card slots-card" aria-labelledby="h-slots">
          <div className="slots-head">
            <h2 id="h-slots" className="card-title">読み取った条件</h2>
            <span aria-live="polite" className="slot-summary">
              <span className={`dot${unconfirmed === 0 ? " ok" : ""}`} id="slotSummaryDot" aria-hidden="true"></span>
              <span id="slotSummary">{summaryText}</span>
            </span>
          </div>
          <div id="confirmRows">
            <SlotRows
              state={state}
              editing={editing}
              onBeginEdit={(row) => setEditing(row.key)}
              onCommitEdit={commitEdit}
              onCancelEdit={() => setEditing(null)}
              onPickIncludesOwner={(value) => {
                setState((prev) => ({ ...prev, participantsIncludesOwner: value === "in" }));
              }}
              errorTarget={error?.target === "location" || error?.target === "time" ? error.target : null}
            />
          </div>
          <div className="extras">
            <p className="extras-label">足せる条件（任意）— 足すほど、届く候補は少なくなります</p>
            <div className="extra-chips" id="extraChips">
              <ExtraChips
                picked={extrasPicked}
                onToggle={(label) => {
                  setExtrasPicked((prev) =>
                    prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label],
                  );
                }}
              />
            </div>
          </div>
          <div className="confirm-actions">
            <button
              type="button"
              className="cta deposit-button"
              id="depositButton"
              aria-describedby="depositHelp"
              disabled={busy || !answered || missing.length > 0}
              aria-busy={String(busy)}
              onClick={() => saveFlowRef.current.saveActive()}
            >
              {busy ? (
                <Icon name="spinner" size={20} />
              ) : (
                <Icon name="latchMark" size={22} className="latch-mark" />
              )}
              <span className="cta-label">{busy ? "預けています…" : "この条件で預ける"}</span>
            </button>
            <a className="ghost rewrite-button" href="#/" data-action="rewrite">書き直す</a>
          </div>
          <p className={ctaHelpClass} id="depositHelp">{ctaHelpText}</p>
          <p className="form-error" id="confirmError" role="alert" hidden={!error}>{error?.note}</p>
        </section>
      </div>
    </section>
  );
};

// あなたの言葉(原文ハイライト・D-03)。旧confirm.jsのrenderWordsと同形
// (mark+行番号sup+vh読み上げ補助)。原文はテキストノードとして組む(XSS安全)。
const WordsText = ({ segments }) => (
  <p className="words-text" id="wordsText">
    「
    {segments.map((segment, i) =>
      segment.no != null ? (
        <mark key={i} className="w">
          {segment.text}
          <sup>
            <span className="vh">（条件</span>
            {segment.no.replace(/^0/, "")}
            <span className="vh">）</span>
          </sup>
        </mark>
      ) : (
        segment.text
      ),
    )}
    」
  </p>
);
