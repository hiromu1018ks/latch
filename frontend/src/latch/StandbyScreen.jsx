// 03「待機中」(UI v2 Phase 6・プロトタイプ03が正源)。旧standby.js(素のDOM配線)
// をReactコンポーネントへ載せ替えたもの。着地状態(?deposited=1)と通常状態の2姿・
// メイン条件カード(pill・原文引用・条件タグ・残時間タイマー1秒更新・条件を直す・
// 取り下げる)・取り下げdialog(chrome.openAppDialog=<dialog class="dlg">・
// 取り下げ済み状態への切替)・オーブと「いま、LATCHがしていること」3カード・
// ほかに待機中の意思リスト(直す/取り下げる)・下書き(D-13)・右上トースト
// (候補が届いたら「見る」→04)。データ源はGET /v1/intents?status=active
// (created_at降順=先頭がメイン)。取り下げ=DELETE /v1/intents/{id}(05 §5)。
// raw_textは所有者への表示のみに使い、ログへ出さない(01 §21)。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  conditionTags,
  depositedAgoText,
  draftAgoText,
  expiryLine,
  formatCategoryPrimary,
  jstParts,
} from "../intent/format.js";
import { hhmmssText } from "./view.js";
import {
  CANDIDATE_TOAST_TEXT,
  DEPOSIT_AGAIN_LABEL,
  DRAFT_EDIT_LABEL,
  DRAFT_PILL_TEXT,
  EDIT_CONDITION_LABEL,
  NO_OTHERS_TEXT,
  STANDBY_DEPOSITED_A,
  STANDBY_DEPOSITED_B,
  STANDBY_DEPOSITED_LEAD,
  STANDBY_LEAD,
  STANDBY_PILL_TEXT,
  STANDBY_TITLE_A,
  STANDBY_TITLE_B,
  WITHDRAW_BUTTON_LABEL,
  WITHDRAW_CANCEL_LABEL,
  WITHDRAW_DIALOG_NOTE,
  WITHDRAW_DIALOG_TITLE,
  WITHDRAWN_NOTE_TEXT,
  WITHDRAWN_PILL_TEXT,
  WITHDRAWN_TOAST_TEXT,
} from "./texts.js";
import { Icon } from "../ui/Icon.jsx";
import { useChrome } from "../ui/chrome.jsx";
import { SurveyCard } from "./SurveyCard.jsx";
import { categoryIconName, splitStatuses } from "./home.js";

const TIMER_TICK_MS = 1000;

// 候補トースト表示済みlatch id(セッション内1回。旧実装のクロージャSetと同じ寿命)
const toastShown = new Set();

// オーブの浮遊チップ(プロトタイプ03の絶対位置。time/予算はIBM Plex Mono)
const ORB_CHIP_POSITIONS = [
  { pos: "top:9%;left:2%", mono: true },
  { pos: "top:15%;right:0", mono: false },
  { pos: "bottom:14%;left:0", mono: false },
  { pos: "bottom:7%;right:6%", mono: true },
];

const posStyle = (pos) =>
  Object.fromEntries(
    pos.split(";").map((pair) => {
      const eq = pair.indexOf(":");
      return [pair.slice(0, eq), pair.slice(eq + 1)];
    }),
  );

// 残時間タイマー(role=timer・1秒更新・HH:MM:SS)
const RemainTimer = ({ deadline }) => {
  const [text, setText] = useState(() =>
    hhmmssText(new Date(deadline).getTime() - Date.now()),
  );
  useEffect(() => {
    const tick = () => setText(hhmmssText(new Date(deadline).getTime() - Date.now()));
    tick();
    const timer = setInterval(tick, TIMER_TICK_MS);
    return () => clearInterval(timer);
  }, [deadline]);
  return (
    <span role="timer" aria-labelledby="remain-label" data-sb-timer data-deadline={deadline}>
      {text}
    </span>
  );
};

// メイン条件カード(旧mainCardHtml)
const MainCard = ({ intent, nowIso, onDrop }) => {
  const structured = intent.structured_intent ?? {};
  const category = formatCategoryPrimary(structured.category);
  const ago = depositedAgoText(intent.created_at, nowIso);
  const tags = conditionTags(structured);
  const clock = intent.expires_at ? jstParts(intent.expires_at) : null;
  const timerLabel = clock
    ? `${clock.hour}:${clock.minute} の取り下げまで`
    : "期限なし";
  return (
    <>
      <div className="sb-main-head">
        <span className="sb-main-cat">
          <Icon name={categoryIconName(structured.category?.primary)} size={16} />
          <span>{category} · {ago}</span>
        </span>
        <span className="sb-pill stb"><span className="dot" aria-hidden="true"></span>{STANDBY_PILL_TEXT}</span>
      </div>
      <p className="sb-main-quote">「{intent.raw_text}」</p>
      {tags.length > 0 ? (
        <div className="sb-tags">
          {tags.map((tag, i) => (
            <span key={i} className="sb-tag">{tag}</span>
          ))}
        </div>
      ) : null}
      <div className="sb-main-foot">
        <div className="sb-timer">
          <span className="sb-timer-label" id="remain-label">{timerLabel}</span>
          {clock ? <RemainTimer deadline={intent.expires_at} /> : null}
        </div>
        <div className="sb-main-actions">
          <a className="ghost sb-edit" href={`#/confirm?intent=${encodeURIComponent(intent.id)}`}>
            <Icon name="pencil" size={15} />
            {EDIT_CONDITION_LABEL}
          </a>
          <button type="button" className="ghost danger" onClick={onDrop} aria-haspopup="dialog">
            {WITHDRAW_BUTTON_LABEL}
          </button>
        </div>
      </div>
    </>
  );
};

// 取り下げ済みのメインカード(削除直後の表示保持・プロトタイプ03のmainGone)
const WithdrawnCard = ({ intent, nowIso }) => (
  <>
    <div className="sb-main-head">
      <span className="sb-main-cat">
        <Icon name={categoryIconName(intent.structured_intent?.category?.primary)} size={16} />
        <span>{formatCategoryPrimary(intent.structured_intent?.category)} · {depositedAgoText(intent.created_at, nowIso)}</span>
      </span>
      <span className="sb-pill gone">{WITHDRAWN_PILL_TEXT}</span>
    </div>
    <p className="sb-main-quote gone">「{intent.raw_text}」</p>
    <div className="sb-main-foot sb-main-foot--gone">
      <span className="sb-gone-note">{WITHDRAWN_NOTE_TEXT}</span>
      <a className="ghost sb-again" href="#/">{DEPOSIT_AGAIN_LABEL}</a>
    </div>
  </>
);

// オーブ(旧orbHtml・条件チップ浮遊)
const Orb = ({ intent }) => {
  const chips = conditionTags(intent.structured_intent ?? {})
    .slice(0, ORB_CHIP_POSITIONS.length)
    .map((tag, i) => ({ ...ORB_CHIP_POSITIONS[i], tag }));
  return (
    <>
      <div className="orb-haze" aria-hidden="true"></div>
      <div className="orb-ring" aria-hidden="true"></div>
      <div className="orb-ring2" aria-hidden="true"></div>
      <div className="orb-core" aria-hidden="true"></div>
      <div className="ripple" aria-hidden="true"></div>
      <div className="ripple d2" aria-hidden="true"></div>
      <div className="ripple d3" aria-hidden="true"></div>
      <svg viewBox="0 0 200 200" fill="none" className="orb-svg" aria-hidden="true">
        <circle cx="100" cy="100" r="74" stroke="#3A4260" strokeWidth="1" strokeDasharray="1.5 7"></circle>
        <g className="arcL"><path d="M100 70 A30 30 0 0 0 100 130" style={{ stroke: "var(--stb)" }} strokeWidth="7" strokeLinecap="round"></path></g>
        <g className="arcR"><path d="M100 70 A30 30 0 0 1 100 130" style={{ stroke: "var(--t1)" }} strokeWidth="7" strokeLinecap="round"></path></g>
      </svg>
      {chips.map(({ pos, mono, tag }, i) => (
        <span key={i} className="orb-chip" data-mono={mono ? "" : undefined} style={posStyle(pos)}>
          {tag}
        </span>
      ))}
    </>
  );
};

// ほかに待機中の意思・下書きの1行(旧othersRowHtml/draftRowHtml)
const StandbyRow = ({ intent, nowIso, first = false, draft = false, onDrop }) => {
  const structured = intent.structured_intent ?? {};
  const meta = draft
    ? [formatCategoryPrimary(structured.category), draftAgoText(intent.created_at, nowIso)]
    : [
        formatCategoryPrimary(structured.category),
        depositedAgoText(intent.created_at, nowIso),
        expiryLine(intent.expires_at, nowIso),
      ];
  return (
    <li className="sb-row" data-border={first ? undefined : "true"}>
      <span className="sb-row-ic" aria-hidden="true">
        <Icon name={categoryIconName(structured.category?.primary)} size={19} />
      </span>
      <div className="sb-row-body">
        <span className="sb-row-text">「{intent.raw_text}」</span>
        <span className="sb-row-meta">{meta.join(" · ")}</span>
      </div>
      <div className="sb-row-actions">
        {draft ? (
          <span className="sb-pill gone">{DRAFT_PILL_TEXT}</span>
        ) : (
          <span className="sb-pill stb"><span className="dot" aria-hidden="true"></span>{STANDBY_PILL_TEXT}</span>
        )}
        <a
          className="ghost sb-row-edit"
          href={`#/confirm?intent=${encodeURIComponent(intent.id)}`}
          aria-label={draft ? `${intent.raw_text}を直して預ける` : `${intent.raw_text}を直す`}
        >
          {draft ? DRAFT_EDIT_LABEL : "直す"}
        </a>
        <button
          type="button"
          className="ghost danger"
          onClick={() => onDrop(intent)}
          aria-haspopup="dialog"
          aria-label={`${intent.raw_text}を取り下げる`}
        >
          {WITHDRAW_BUTTON_LABEL}
        </button>
      </div>
    </li>
  );
};

export const StandbyScreen = ({ client }) => {
  const { showToast, setCounts, openAppDialog } = useChrome();
  const [items, setItems] = useState([]); // active intents(created_at降順)
  const [drafts, setDrafts] = useState([]); // 下書き intents(D-13・created_at降順)
  const [goneMain, setGoneMain] = useState(null); // 取り下げ直後のメインカードの表示保持
  const [loaded, setLoaded] = useState(false);
  // ?deposited=1は02からの着地状態(コンポーネントはルート入部で1回マウント)
  const [deposited] = useState(
    () =>
      new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("deposited") === "1",
  );

  // --- 候補トースト(03の右上・見る→04)と候補/成立バッジ -----------------------
  const refreshLatches = useCallback(async () => {
    const data = await client.call("GET", "/v1/latches?limit=20");
    const { candidates, matched } = splitStatuses(data.items);
    setCounts({ proposals: candidates.length, matched: matched.length });
    const newest = candidates[0];
    if (newest && !toastShown.has(newest.id)) {
      toastShown.add(newest.id);
      showToast({
        message: CANDIDATE_TOAST_TEXT,
        href: `#/latches/${newest.id}`,
      });
    }
  }, [client, setCounts, showToast]);

  // --- 画面へ入る(一覧取得) ---------------------------------------------------
  useEffect(() => {
    let alive = true;
    (async () => {
      const [active, draftPage] = await Promise.all([
        client.call("GET", "/v1/intents?status=active"),
        client.call("GET", "/v1/intents?status=draft").catch(() => ({ items: [] })),
      ]);
      if (!alive) return;
      setItems(active.items);
      setDrafts(draftPage.items);
      setGoneMain(null); // 画面再訪ではサーバの真実に揃える(削除済みは存在しない)
      setCounts({ standby: active.items.length });
      setLoaded(true);
    })();
    refreshLatches().catch(() => {}); // 失敗時はトースト・バッジ随伴なし
    return () => {
      alive = false;
    };
  }, [client, setCounts, refreshLatches]);

  // --- 取り下げ(<dialog class="dlg">→DELETE /v1/intents/{id}) ------------------
  // 対象は待機中と下書きの両方から探す(D-13)
  const askDrop = (target) => {
    openAppDialog({
      title: WITHDRAW_DIALOG_TITLE,
      body: (
        <>
          <p className="dlg-quote">「{target.raw_text}」</p>
          <p className="dlg-desc">{WITHDRAW_DIALOG_NOTE}</p>
        </>
      ),
      actions: [
        { label: WITHDRAW_CANCEL_LABEL, variant: "ghost" },
        { label: WITHDRAW_BUTTON_LABEL, variant: "danger", onClick: () => withdraw(target) },
      ],
    });
  };

  const withdraw = async (target) => {
    if (!target) return;
    const wasMain = items[0]?.id === target.id;
    try {
      await client.call("DELETE", `/v1/intents/${target.id}`);
      const nextItems = items.filter((item) => item.id !== target.id);
      setItems(nextItems);
      setDrafts((prev) => prev.filter((item) => item.id !== target.id));
      // ヘッダー/タブバーの待機中バッジも 新件数へ追従させる
      // (画面内のバッジだけでなくchrome側へも供給する・phase 7実機E2Eで検出)
      setCounts({ standby: nextItems.length });
      if (wasMain) setGoneMain(target); // 取り下げ済みカードを表示し続ける
      showToast(WITHDRAWN_TOAST_TEXT);
      await refreshLatches().catch(() => {}); // 候補消滅をバッジへ反映
    } catch {
      showToast("取り下げできませんでした。もう一度お試しください。");
    }
  };

  const main = items[0] ?? null;
  const others = items.slice(1);
  const nowIso = new Date().toISOString();

  // 旧実装はshow()の完了まで描画が走らなかったため、取得中は骨組みだけを置く
  // (空状態の誤表示を避ける。見た目への影響なし)
  if (!loaded) {
    return <div className="page-main page-main--standby" id="standbyScreen"></div>;
  }

  return (
    <div className="page-main page-main--standby" id="standbyScreen">
      <section className="sb-hero" aria-labelledby="h-standby">
        <div className="sb-col">
          <p className="sb-badge"><span className="beacon" aria-hidden="true"></span><span data-sb-badge>待機中 · {items.length}件</span></p>
          <h1 id="h-standby" className="sb-title">
            <span data-sb-title-a>{deposited ? STANDBY_DEPOSITED_A : STANDBY_TITLE_A}</span>
            <br />
            <span data-sb-title-b>{deposited ? STANDBY_DEPOSITED_B : STANDBY_TITLE_B}</span>
          </h1>
          <p className="sb-lead" data-sb-lead>{deposited ? STANDBY_DEPOSITED_LEAD : STANDBY_LEAD}</p>
          <article
            className="sb-main"
            data-sb-main
            aria-label={goneMain || !main ? undefined : `預けた条件（${formatCategoryPrimary(main.structured_intent?.category)}）`}
            hidden={goneMain == null && main == null}
          >
            {goneMain ? (
              <WithdrawnCard intent={goneMain} nowIso={nowIso} />
            ) : main ? (
              <MainCard intent={main} nowIso={nowIso} onDrop={() => askDrop(main)} />
            ) : null}
          </article>
        </div>
        <div className="sb-orb" data-sb-orb aria-hidden="true" hidden={goneMain != null || main == null}>
          {main ? <Orb intent={main} /> : null}
        </div>
      </section>

      {/* 体感アンケート(V1-P3・05 v0.7)。dueのときだけ受動カードとして出る */}
      <SurveyCard client={client} />

      <section className="sb-doing" aria-labelledby="h-doing">
        <h2 id="h-doing" className="sb-h2">いま、LATCHがしていること</h2>
        <div className="doing-grid">
          <div className="doing-card">
            <span aria-hidden="true" className="doing-no">01</span>
            <h3>照らし合わせ続ける</h3>
            <p>あなたの条件を、ほかの待機中の意思と照らし合わせ続けています。そのあいだ、あなたの条件は誰にも表示されません。</p>
          </div>
          <div className="doing-card">
            <span aria-hidden="true" className="doing-no">02</span>
            <h3>成立し得るかだけを見る</h3>
            <p>見るのは人柄や相性ではなく、その時・その条件で二つの意思が成立し得るか。片方だけが大満足する提案は出しません。</p>
          </div>
          <div className="doing-card">
            <span aria-hidden="true" className="doing-no">03</span>
            <h3>揃ったら、ひとことだけ</h3>
            <p>通知の本文はこの一文だけ。ロック画面に意思が漏れることはありません。</p>
            <div className="doing-notify" aria-hidden="true">
              <span className="doing-notify-ic">
                <svg width="18" height="18" viewBox="0 0 40 40" fill="none"><path d="M19 5 A15 15 0 0 0 19 35" style={{ stroke: "var(--t1)" }} strokeWidth="4" strokeLinecap="round"></path><path d="M22 5 A15 15 0 0 1 22 35" style={{ stroke: "var(--acc)" }} strokeWidth="4" strokeLinecap="round"></path></svg>
              </span>
              <span className="doing-notify-body"><span className="doing-notify-app">LATCH</span><span className="doing-notify-text">LATCH候補があります</span></span>
              <span className="doing-notify-time">今</span>
            </div>
          </div>
        </div>
      </section>

      <section className="sb-others-sec" aria-labelledby="h-others">
        <div className="sb-others-head">
          <h2 id="h-others" className="sb-h2">ほかに待機中の意思</h2>
          <a className="sb-new" href="#/">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>新しく預ける
          </a>
        </div>
        <ul className="sb-others" data-sb-others>
          {others.length
            ? others.map((intent, i) => (
                <StandbyRow key={intent.id} intent={intent} nowIso={nowIso} first={i === 0} onDrop={askDrop} />
              ))
            : <li className="sb-row-empty">{NO_OTHERS_TEXT}</li>}
        </ul>
      </section>

      {/* 下書き(D-13)。1件もないときはセクションごと隠す(空状態の規定なし) */}
      <section className="sb-others-sec" data-sb-drafts-section aria-labelledby="h-drafts" hidden={drafts.length === 0}>
        <div className="sb-others-head">
          <h2 id="h-drafts" className="sb-h2">下書き</h2>
        </div>
        <ul className="sb-others" data-sb-drafts>
          {drafts.map((intent, i) => (
            <StandbyRow key={intent.id} intent={intent} nowIso={nowIso} first={i === 0} draft onDrop={askDrop} />
          ))}
        </ul>
      </section>
    </div>
  );
};
