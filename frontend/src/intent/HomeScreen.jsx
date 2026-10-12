// 01「預ける(ホーム)」(UI v2 Phase 6・プロトタイプ01が正源)。旧screen.js
// (素のDOM配線)とhome.jsのcreateHome(intentCardHtml)をコンポーネントへ載せ替えた
// もの。composerカード(フォーカス時の枠+glow)・読み取りchips(aria-live=polite)・
// 例文chips・CTA「読み解く」→#/confirm・下書き保存(D-13)・待機中の意思カード
// 一覧(候補との対応づけが確定できるときだけ04直結)。フォームstate(state)は
// 02と共有し、更新はApp経由のsetStateで行う(React流の単一の真実)。
// 意図文言をログに出さない(01 §21)。
import { useEffect, useRef, useState } from "react";
import { applyParseFallback, applyParseResult, canDraft } from "./state.js";
import { createParseFlow } from "./parseFlow.js";
import { readSlots } from "./conditions.js";
import { createSaveFlow } from "./save.js";
import {
  DEFAULT_VISIBILITY,
  conditionTags,
  depositedAgoText,
  expiryLine,
  formatCategoryPrimary,
} from "./format.js";
import {
  notificationLevelFor,
  readNearbyEnabled,
} from "../latch/nearbyPreference.js";
import { useChrome } from "../ui/chrome.jsx";
import { Icon } from "../ui/Icon.jsx";
import { pairCandidateLatch, splitStatuses, categoryIconName } from "../latch/home.js";

// 例文chips(プロトタイプ01の固定4値)
export const EXAMPLES = [
  { tag: "飲み", text: "今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい" },
  { tag: "転職", text: "年収1,000万円以上なら転職を考える" },
  { tag: "売買", text: "この車が190万円以上なら売ってもいい" },
  { tag: "集まり", text: "4人集まるなら参加したい" },
];

export const HomeScreen = ({ client, state, getState, setState }) => {
  const { showToast, setCounts } = useChrome();
  const [intents, setIntents] = useState([]);
  const [latchItems, setLatchItems] = useState([]);
  const [parseError, setParseError] = useState(null); // {message, retryable} | null
  const [busy, setBusy] = useState(false); // CTA「読み解く」の処理中
  const textareaRef = useRef(null);

  // stateの最新値(parseFlowのコールバックやCTAの同期待ちから触る)
  const stateRef = useRef(state);
  stateRef.current = state;

  // --- parse連携(debounce・design §2.4) --------------------------------------
  const parseFlowRef = useRef(null);
  if (!parseFlowRef.current) {
    parseFlowRef.current = createParseFlow({
      send: (text, { signal }) =>
        client.call("POST", "/v1/intents/parse", { body: { text }, signal }),
      onResult: (data) => {
        setParseError(null);
        setState((prev) => applyParseResult(prev, data.structured_intent));
      },
      onError: (err) => {
        setState((prev) => ({ ...prev, parseStatus: "error" }));
        if (err?.code === "VALIDATION_ERROR") {
          // 構造化フォームへのフォールバック(確定値9): 02の行を手動入力で埋める
          setParseError(null);
          setState((prev) => applyParseFallback(prev));
          return;
        }
        if (err?.code === "RATE_LIMITED") {
          setParseError({
            message: "操作が集中しています。少し時間をおいてから入力し直してください。",
            retryable: false,
          });
        } else if (err?.code === "LLM_UNAVAILABLE") {
          setParseError({ message: "ただいま条件を読み取れません。", retryable: true });
        } else if (err?.code === "UNAUTHENTICATED") {
          return; // パネル表示済み(onSessionExpired)
        } else {
          setParseError({ message: "通信エラーが発生しました。", retryable: true });
        }
      },
    });
  }

  // --- 入力(制御textarea・chipsとCTAはstateから派生) -------------------------
  const onInput = (value) => {
    setState((prev) => ({ ...prev, rawText: value })); // chipsの空判定が1イベント遅れないよう先に更新
    parseFlowRef.current.input(value);
  };

  const goConfirm = async () => {
    const slots = readSlots(stateRef.current, new Date().toISOString());
    if (busy || slots.length === 0) return;
    setBusy(true);
    try {
      await parseFlowRef.current.commit(); // debounce中の入力を即時確定(古い結果での遷移を防ぐ)
    } catch {
      // parse失敗時はこの後のslotCount判定で遷移を見合わせる
    }
    setBusy(false);
    const committed = readSlots(stateRef.current, new Date().toISOString());
    if (committed.length > 0) location.hash = "#/confirm";
  };

  // ⌘Enterは維持(表示ヒントなし)
  useEffect(() => {
    const onKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        goConfirm();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  // --- 下書き保存(D-13: 01のcomposer直下の最小導線・APIは温存) ---------------
  const [draftBusy, setDraftBusy] = useState(false);
  const saveFlowRef = useRef(null);
  if (!saveFlowRef.current) {
    saveFlowRef.current = createSaveFlow({
      client,
      getState,
      getOptions: () => ({
        visibility: DEFAULT_VISIBILITY, // D-02: 選択UIなし・既定値で確定送信
        notificationLevel: notificationLevelFor(readNearbyEnabled()), // 03 v0.6: 設定トグル経由のみ
        expiresAt: null,
      }),
      onBusy: setDraftBusy,
      onDraftSaved: () => {
        showToast("下書きを保存しました");
      },
      onActiveSaved: () => {},
      onFormError: () => {
        showToast("下書きを保存できませんでした");
      },
    });
  }

  // --- 待機中の意思(GET /v1/intents?status=active)+バッジ供給 ----------------
  useEffect(() => {
    let alive = true;
    client
      .call("GET", "/v1/intents?status=active")
      .then((data) => {
        if (!alive) return;
        setIntents(data.items);
        setCounts({ standby: data.items.length });
      })
      .catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
    client
      .call("GET", "/v1/latches?limit=20")
      .then((data) => {
        if (!alive) return;
        setLatchItems(data.items);
        const { candidates, matched } = splitStatuses(data.items);
        setCounts({ proposals: candidates.length, matched: matched.length });
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [client, setCounts]);

  // --- 描画に必要な派生値 -----------------------------------------------------
  const nowIso = new Date().toISOString();
  const slots = readSlots(state, nowIso);
  const empty = state.rawText.trim().length === 0;
  const noread = !empty && slots.length === 0;
  const slotCount = slots.length;
  const { candidates } = splitStatuses(latchItems);
  const paired = pairCandidateLatch(intents, candidates);

  const ctaHelpText = busy
    ? "時間・場所・人数・金額に分けています"
    : empty
      ? "条件を書くと、読み解けます"
      : slotCount === 0
        ? "読み取れる条件が1つ以上必要です"
        : "次の画面で、読み取り結果を確認できます";

  return (
    <div className="page-main" id="homeScreen">
      <div className="haze haze-home" aria-hidden="true"></div>

      <section className="compose-section" aria-labelledby="h-compose">
        <p className="standby-line" id="standbyLine" hidden={intents.length === 0}>
          <span className="beacon" aria-hidden="true"></span>
          <span data-standby-count>
            {intents.length > 0
              ? `${intents.length}件の意思が待機中 — いまは何もしなくて大丈夫`
              : "0件の意思が待機中 — いまは何もしなくて大丈夫"}
          </span>
        </p>
        <h1 id="h-compose" className="hero-title">どんな条件なら、<br />動きますか。</h1>
        <p className="hero-lead">ひとこと預けるだけで、探す必要はありません。条件が揃った瞬間に、相手と機会のほうから届きます。</p>

        <div className="composer">
          <label htmlFor="intentText" className="composer-label">条件をひとことで</label>
          <textarea
            id="intentText"
            ref={textareaRef}
            rows="3"
            maxLength={300}
            aria-describedby="readStatus"
            placeholder="例：今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい"
            value={state.rawText}
            onChange={(event) => onInput(event.target.value)}
          ></textarea>

          <div className="read-section">
            <p className="read-head"><span className="read-dot" aria-hidden="true"></span>LATCHの読み取り（入力に合わせて更新）</p>
            <div id="readStatus" aria-live="polite" className="read-status">
              <div id="readChips" className="read-chips">
                {slots.map((slot) => (
                  <span key={slot.key} className="chip">
                    <span className="chip-label">{slot.label}</span>
                    <span className="chip-value">{slot.value}</span>
                  </span>
                ))}
              </div>
              <span className="read-guide" data-guide hidden={!empty}>時間・場所・人数・金額などを書くと、ここで読み取ります</span>
              <span className="read-noread" data-noread hidden={!noread}>
                <Icon name="info" size={18} />
                <span>まだ条件を読み取れません。「今日20時以降」「天文館で」「3人」「5,000円まで」のように、時間・場所・人数・金額のどれかを足してみてください。</span>
              </span>
            </div>
            <p className="parse-error" id="parseError" role="alert" hidden={!parseError}>
              {parseError?.message}
              {parseError?.retryable ? (
                // 07 D-17: 入力テキストを保持した再試行
                <button type="button" className="parse-retry" onClick={() => parseFlowRef.current.retry()}>
                  もう一度読み取る
                </button>
              ) : null}
            </p>
          </div>

          <div className="composer-foot">
            <div className="composer-foot-left">
              <p className="lock-line">
                <Icon name="lock" size={16} />
                <span>成立するまで、この条件は誰にも見えません</span>
              </p>
              <button
                type="button"
                className="ghost draft-button"
                id="draftButton"
                disabled={busy || draftBusy || !canDraft(state)}
                onClick={() => saveFlowRef.current.saveDraft()}
              >
                下書きに保存
              </button>
            </div>
            <div className="cta-wrap">
              <button
                type="button"
                className="cta read-button"
                id="readButton"
                aria-describedby="ctaHelp"
                disabled={busy || slotCount === 0}
                aria-busy={String(busy)}
                onClick={() => goConfirm()}
              >
                {busy ? <Icon name="spinner" size={18} /> : <Icon name="arrowRight" size={18} />}
                <span className="cta-label">{busy ? "読み解いています…" : "読み解く"}</span>
              </button>
              <span className="cta-help" id="ctaHelp">{ctaHelpText}</span>
            </div>
          </div>
        </div>

        <div className="examples">
          <p className="examples-label">こんな預け方もできます</p>
          <div className="example-chips" id="exampleChips">
            {EXAMPLES.map((example) => (
              <button
                key={example.tag}
                type="button"
                className="example-chip ghost"
                onClick={() => {
                  onInput(example.text);
                  textareaRef.current?.focus();
                }}
              >
                <span className="example-tag">{example.tag}</span>
                {example.text}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* 0件はヒーロー行とセクションごと隠す(プロトタイプに空状態の規定なし) */}
      <section className="wait-section" id="waitSection" aria-labelledby="h-wait" hidden={intents.length === 0}>
        <div className="wait-head">
          <div className="wait-title">
            <h2 id="h-wait" className="wait-heading">待機中の意思</h2>
            <span className="wait-sub">あなたが預けて、まだ成立していない条件</span>
          </div>
          <a className="wait-all" href="#/standby">すべて見る
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path></svg>
          </a>
        </div>
        <div className="intent-grid" id="intentCards">
          {intents.map((intent) => {
            const emphasized = paired != null && paired.intentId === intent.id;
            return (
              <IntentCard
                key={intent.id}
                intent={intent}
                nowIso={nowIso}
                proposalCount={emphasized ? candidates.length : 0}
                proposalHref={emphasized ? `#/latches/${paired.latch.id}` : null}
              />
            );
          })}
        </div>
      </section>
    </div>
  );
};

// 待機中の意思カード(旧intentCardHtmlのReact版・プロトタイプ01)。
// raw_textは所有者への表示のみに使い、ログへ出さない(01 §21)。
export const IntentCard = ({ intent, nowIso, proposalCount = 0, proposalHref = null }) => {
  const structured = intent.structured_intent ?? {};
  const category = formatCategoryPrimary(structured.category);
  const ago = depositedAgoText(intent.created_at, nowIso);
  const tags = conditionTags(structured);
  // 候補N件=acc強調カード(プロトタイプ01)。対応づけが確定できる
  // (active1件×候補あり)ときだけ04へ直結し、それ以外は03へ
  const emphasized = proposalCount > 0;
  const href = emphasized && proposalHref ? proposalHref : "#/standby";
  return (
    <a className={`intent-card lift${emphasized ? " acc" : ""}`} href={href}>
      <div className="intent-card-head">
        <span className="intent-card-cat">
          <Icon name={categoryIconName(structured.category?.primary)} size={16} />
          <span>{category} · {ago}</span>
        </span>
        {emphasized ? (
          <span className="intent-pill acc">候補 {proposalCount}件</span>
        ) : (
          <span className="intent-pill stb"><span className="dot" aria-hidden="true"></span>待機中</span>
        )}
      </div>
      <p className="intent-quote">「{intent.raw_text}」</p>
      {tags.length > 0 ? (
        <div className="intent-tags">
          {tags.map((tag, i) => (
            <span key={i} className="intent-tag">{tag}</span>
          ))}
        </div>
      ) : null}
      <div className="intent-foot">
        <span>{expiryLine(intent.expires_at, nowIso)}</span>
        <span className={`intent-foot-see${emphasized ? " acc" : ""}`}>
          {emphasized ? (
            <>候補を見る<Icon name="arrowRight" size={15} /></>
          ) : (
            "静かに待機中"
          )}
        </span>
      </div>
    </a>
  );
};
