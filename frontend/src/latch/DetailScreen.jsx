// LATCH詳細 — 04「候補が届く」提案姿+05「成立・チャット」(UI v2 Phase 6・
// プロトタイプ04/05が正源)。旧detail.js(素のDOM配線)をReactコンポーネントへ
// 載せ替えたもの。1画面3姿 — statusで分岐し、ページ内で状態が変わる場面
// (回答・undo・締切・成立取消)はすべて再取得で収束する(表示の唯一の真実は
// 詳細応答・design §2.5)。
// 提案姿: ヒーロー・4情報カード・照合表(自分の条件×候補の対比較)・回答期限
// タイマー(残5分でdng色)・回答2択(D-05)・4状態(open/joined/allIn/declined)・
// undo(D-06)・理由chips(D-07)・目隠しアバター。
// 見送りは「今回は見送る」で即座には送らない — 理由chipsはdefer回答のbodyに
// のみ受理される(05 §5)ため、declined状態の離脱時(「待機中に戻る」・画面遷移)
// に理由といっしょに送る。取り消し(送信前)は画面上の取消のみでAPI呼び出しなし。
// 成立姿: ヒーロー(snap/burstアニメ・条件タグ)+aside(成立した内容+カレンダーに
// 追加=D-10・メンバー・安全のために)+チャット。成立の取り消し(D-08)は
// POST /v1/latches/{id}/leave — サーバがチャットへシステム行を挿入し、自分は
// 以後書込不可(CHAT_READONLY)。詳細応答にleft_user_idsは出ないため取消後の
// 姿はセッション内のフラグで保持する(ALREADY_LEFTでも同様に切替える)。
import { useCallback, useEffect, useMemo, useRef, useState, Fragment } from "react";
import {
  ALL_IN_LEAD_TEXT,
  ALL_IN_TITLE,
  BACK_TO_STANDBY_LABEL,
  CANCEL_DIALOG_CONFIRM,
  CANCEL_DIALOG_DISMISS,
  CANCEL_DIALOG_INTRO,
  CANCEL_DIALOG_NOTE,
  CANCEL_DIALOG_TITLE,
  CANCEL_ENTRY_TEXT,
  DEADLINE_LINE_SUFFIX,
  DECLINED_NOTE_TEXT,
  DECLINED_TITLE,
  FIT_ALL_TEXT,
  FIT_NOTE_TEXT,
  FIT_TITLE,
  FIT_UNAVAILABLE_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  HOME_LINK_TEXT,
  JOINED_TITLE,
  JOINED_WAIT_NOTE,
  LATCHED_DETAILS_TITLE,
  LATCHED_PLACE_SUB_TEXT,
  LATCHED_TITLE,
  LEFT_LEAD_TEXT,
  LEFT_TITLE,
  LATCH_NOT_FOUND_TEXT,
  MEMBER_JOINED_TEXT,
  MEMBER_LEFT_TEXT,
  MEMBER_NOTE_TEXT,
  MINIMAL_PROPOSAL_NOTE,
  OPEN_CHAT_LABEL,
  PROPOSAL_LEAD_TEXT,
  RATE_LIMIT_TEXT,
  REASON_CHIPS,
  REASON_LABEL_TEXT,
  REASON_NOTE_TEXT,
  RESPONSE_BUTTONS,
  RESPONSE_NOTE_TEXT,
  SAFETY_ENTRY_TEXT,
  SAFETY_GUIDE_ITEMS,
  SAFETY_GUIDE_TITLE,
  SAFETY_HEADING_TEXT,
  UNDO_BLOCKED_TOAST_TEXT,
  UNDO_DECLINED_LABEL,
  UNDO_JOINED_LABEL,
} from "./texts.js";
import {
  cancelDialogLines,
  chatMembers,
  clockText,
  closedText,
  countdownText,
  fitRows,
  formatYen,
  isMinimalProposal,
  joinedWaitText,
  latchMode,
  latchedRows,
  latchedTags,
  pickMyIntent,
  proposalStateOf,
  proposalTimeParts,
  remainingMs,
  whoLineText,
} from "./view.js";
import { createRespondFlow } from "./respond.js";
import { downloadIcs, hasCalendarTime } from "./calendar.js";
import { Icon } from "../ui/Icon.jsx";
import { useChrome } from "../ui/chrome.jsx";
import { Chat } from "./Chat.jsx";
import { SafetyDialog } from "./SafetyDialog.jsx";
import { AttendanceCard } from "./AttendanceCard.jsx";

const DEADLINE_TICK_MS = 1000; // 04のタイマーは分:秒・1秒更新(プロトタイプ04)
const TIMER_LOW_MS = 5 * 60 * 1000; // 残5分でdng色(timer-low)

const initialOf = (name) => Array.from(String(name ?? "?"))[0] ?? "?";

export const DetailScreen = ({ client, appState, latchId }) => {
  const { showToast, openAppDialog, setCurrentNav } = useChrome();
  const [latch, setLatch] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState(null); // .detail-error(role=alert)
  const [view, setView] = useState(null); // "proposal" | "matched" | "closed"
  const [myIntent, setMyIntent] = useState(null);
  const [me, setMe] = useState(null);
  const [declinePending, setDeclinePending] = useState(false); // 見送り未送信(離脱時に送る)
  const [declineReasons, setDeclineReasons] = useState(() => new Set());
  const [respondBusy, setRespondBusy] = useState(false);
  const [declineSending, setDeclineSending] = useState(false);
  const [deadlineExpired, setDeadlineExpired] = useState(false);
  const [leftLatch, setLeftLatch] = useState(false); // 参加持取り消し済み(D-08)
  const [safety, setSafety] = useState(null); // SafetyDialogへの入力(null=閉じている)

  // 再取得をまたいで保持する非state値(旧実装のクロージャ変数と同じ役割)
  const showAllInOnceRef = useRef(false); // yes回答で成立した直後=04のallInを見せてから05へ
  const matchedAtRef = useRef(null); // 成立時刻(yes直後のみ実時刻。APIにmatched_atはない)
  const declinePendingRef = useRef(false);
  declinePendingRef.current = declinePending;
  const declineReasonsRef = useRef(declineReasons);
  declineReasonsRef.current = declineReasons;
  const latchRef = useRef(null);
  latchRef.current = latch;
  const sendDeclineRef = useRef(null);

  const fetchLatch = useCallback(
    () => client.call("GET", `/v1/latches/${latchId}`),
    [client, latchId],
  );

  const showError = useCallback((message) => setError(message), []);

  // -- 再取得(回答成功・409系の収束先 — 表示の唯一の真実は詳細応答) -------------
  const refreshRef = useRef(null);
  const renderLatch = useCallback(
    (nextLatch, alive = true) => {
      if (!alive) return;
      latchRef.current = nextLatch;
      setLatch(nextLatch);
      setNotFound(false);
      setDeclineReasons(new Set()); // chipsの選択は送信時に消費する。描画は常にサーバの真実から
      const mode = latchMode(nextLatch);
      // yes回答で成立した直後は04のallIn状態(チャットを開く)を一度だけ出す。
      // 直接訪問・再読み込みは05(チャット)へ
      if (mode === "proposal" || (mode === "matched" && showAllInOnceRef.current)) {
        setDeclinePending(false); // 描画は常にサーバの真実から(未送信の見送りは離脱で送信済み)
        setView("proposal");
        setMyIntent(null);
      } else if (mode === "matched") {
        setView("matched");
      } else {
        setView("closed");
      }
    },
    [],
  );
  const refresh = useCallback(async () => {
    // 再取得失敗時は現在の表示を維持(次の操作で再試行)
    try {
      const data = await fetchLatch();
      renderLatch(data.latch);
    } catch {
      // 現在の表示を維持
    }
  }, [fetchLatch, renderLatch]);
  refreshRef.current = refresh;

  const respondFlow = useMemo(
    () => createRespondFlow({ client, latchId, refresh: () => refreshRef.current?.(), showError }),
    [client, latchId, showError],
  );

  // -- 見送りの送信(declined状態の離脱時・D-07) --------------------------------
  const sendDecline = useCallback(
    async ({ navigate = false } = {}) => {
      if (!declinePendingRef.current) return;
      declinePendingRef.current = false;
      setDeclinePending(false);
      const reasons = [...declineReasonsRef.current];
      const result = await respondFlow.submit("defer", { reasons });
      if (result.ok) {
        if (navigate) location.hash = "#/standby";
        return;
      }
      if (navigate) {
        // 画面遷移の解除では画面が出ていないためトーストで伝える
        showToast(
          result.kind === "conflict"
            ? "この候補はすでに閉じています"
            : "見送りを送信できませんでした",
        );
      }
    },
    [respondFlow, showToast],
  );
  sendDeclineRef.current = sendDecline;

  // ルート離脱時にAppから呼ぶ(未送信の見送りを送る+タイマー停止はunmount側)
  useEffect(() => {
    let alive = true;
    // 再訪時はセッションフラグを捨てる(サーバの真実に揃える)
    showAllInOnceRef.current = false;
    matchedAtRef.current = null;
    setLeftLatch(false);
    setLatch(null);
    setNotFound(false);
    setError(null);
    (async () => {
      try {
        const data = await fetchLatch();
        renderLatch(data.latch, alive);
      } catch (err) {
        if (!alive) return;
        // 404/403は同一扱い(参加者でない事実の開示を避ける・引用#16)。
        // 通信エラーも導線つきの案内へ(再訪で再試行)
        setNotFound(true);
        setCurrentNav("proposals");
      }
    })();
    return () => {
      alive = false;
      // 画面離脱時の未送信見送り(D-07)
      sendDeclineRef.current?.({});
    };
  }, [client, latchId, fetchLatch, renderLatch, setCurrentNav]);

  // 提案姿(全形)での照合表の左列はactive Intentからの値マッチングで特定する
  // (詳細応答にintent_idsがない・gap分析§6。最小形では照合表ごと出さない)
  const minimal = latch ? isMinimalProposal(latch) : false;
  useEffect(() => {
    if (!latch || view !== "proposal" || isMinimalProposal(latch)) return;
    let alive = true;
    client
      .call("GET", "/v1/intents?status=active")
      .then((data) => {
        if (alive) setMyIntent(pickMyIntent(data.items, latch));
      })
      .catch(() => {
        if (alive) setMyIntent(null);
      });
    appState
      .ensureMe()
      .then((m) => {
        if (alive) setMe(m);
      })
      .catch(() => {
        if (alive) setMe(null);
      });
    return () => {
      alive = false;
    };
  }, [latch, view, client, appState]);

  // 成立姿(チャット)は自分のidが要る(メンバーの自分/他者判定)
  useEffect(() => {
    if (!latch || view !== "matched") return;
    let alive = true;
    appState
      .ensureMe()
      .then((m) => {
        if (alive) setMe(m);
      })
      .catch(() => {
        if (alive) setMe(null);
      });
    return () => {
      alive = false;
    };
  }, [latch, view, appState]);

  // nav強調を実態に合わせる(提案=候補・成立=成立・不成立=候補の流れの終着)
  useEffect(() => {
    if (notFound) {
      setCurrentNav("proposals");
      return;
    }
    if (!latch) return;
    const mode = latchMode(latch);
    setCurrentNav(mode === "matched" && view === "matched" ? "matched" : "proposals");
  }, [latch, view, notFound, setCurrentNav]);

  // -- 回答・undoの操作 --------------------------------------------------------
  const submitResponse = async (value) => {
    if (respondBusy) return;
    if (value === "yes") {
      // 全員がそろった成立は04のallIn状態(チャットを開く→05)へ一度だけ寄り道する
      showAllInOnceRef.current = true;
      matchedAtRef.current = new Date().toISOString(); // 成立時刻(APIにmatched_atはない)
    } else {
      // 見送る=即座には送らない(理由chips→離脱時に送信・D-07)
      setDeclinePending(true);
      setDeclineReasons(new Set());
      return;
    }
    setRespondBusy(true);
    try {
      await respondFlow.submit(value);
    } finally {
      setRespondBusy(false);
    }
  };

  const undo = async () => {
    if (respondBusy) return;
    setRespondBusy(true);
    try {
      const result = await respondFlow.undo();
      // undo後は再取得でopen姿へ。409(成立済み・期限経過)はトーストで伝える
      if (!result.ok && result.kind === "conflict") {
        showToast(UNDO_BLOCKED_TOAST_TEXT);
      }
    } finally {
      setRespondBusy(false);
    }
  };

  const confirmDecline = async () => {
    setDeclineSending(true);
    try {
      await sendDeclineRef.current({ navigate: true });
    } finally {
      setDeclineSending(false);
    }
  };

  const cancelDecline = () => {
    // 送信前の取り消し=画面操作のみ(API呼び出しなし)
    setDeclinePending(false);
    setDeclineReasons(new Set());
  };

  // -- 成立の取り消し(D-08)dialog — chrome.openAppDialog(<dialog class="dlg">) --
  const confirmLeave = async () => {
    try {
      await client.call("POST", `/v1/latches/${latchId}/leave`, { body: {} });
    } catch (err) {
      if (err?.status === 409 && err?.code === "ALREADY_LEFT") {
        setLeftLatch(true); // 取り消し済み — 取り消し後の姿へ収束させる
        await refreshRef.current?.();
        return;
      }
      showToast(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : "取り消せませんでした。");
      return;
    }
    setLeftLatch(true);
    await refreshRef.current?.(); // 再取得で05の取り消し後姿へ(チャット再同期でシステム行も出る)
  };

  const openCancelDialog = () => {
    openAppDialog({
      title: CANCEL_DIALOG_TITLE,
      body: (
        <div className="dlg-desc">
          <p>{CANCEL_DIALOG_INTRO}</p>
          <ul className="dlg-list">
            {cancelDialogLines(latchRef.current, appState.me?.id ?? null).map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          <p className="dlg-note">{CANCEL_DIALOG_NOTE}</p>
        </div>
      ),
      actions: [
        { label: CANCEL_DIALOG_DISMISS, variant: "ghost" },
        {
          label: CANCEL_DIALOG_CONFIRM,
          variant: "danger",
          onClick: () => void confirmLeave(),
        },
      ],
    });
  };

  // -- 描画 --------------------------------------------------------------------
  const meId = me?.id ?? appState.me?.id ?? null;
  const meInitial = initialOf(me?.display_name ?? appState.me?.display_name ?? "?");

  let className = "detail-view";
  let inner = null;
  if (notFound) {
    inner = (
      <section className="latch-detail latch-notfound">
        <div className="closed-card">
          <p className="section-kicker"><span></span>LATCH</p>
          <p className="closed-text">{LATCH_NOT_FOUND_TEXT}</p>
          <a className="ghost sb-again closed-home" href="#/">{HOME_LINK_TEXT}</a>
        </div>
      </section>
    );
  } else if (latch && view === "proposal") {
    className = "page-main page-main--proposal";
    inner = (
      <ProposalView
        latch={latch}
        myIntent={myIntent}
        meInitial={meInitial}
        declinePending={declinePending}
        declineReasons={declineReasons}
        respondBusy={respondBusy}
        declineSending={declineSending}
        deadlineExpired={deadlineExpired}
        onExpire={() => setDeadlineExpired(true)}
        onRespond={submitResponse}
        onToggleReason={(value) => {
          setDeclineReasons((prev) => {
            const next = new Set(prev);
            if (next.has(value)) next.delete(value);
            else next.add(value);
            return next;
          });
        }}
        onConfirmDecline={confirmDecline}
        onCancelDecline={cancelDecline}
        onUndo={undo}
        onOpenChat={() => {
          // チャットを開いた時点でallInの一度きり表示は消費する。開いた後の
          // 再取得(参加取り消しなど)でallInカードへ戻さない(phase 7の実機E2Eで検出)
          showAllInOnceRef.current = false;
          setView("matched");
        }}
        onOpenSafety={() =>
          setSafety({
            latchId: latch.id,
            // participants=null(提案1対1)はreportee_id省略body(案X)+ブロック導線なし
            participants: null,
            meId,
          })
        }
      />
    );
  } else if (latch && view === "matched") {
    className = "page-main page-main--latched";
    inner = (
      <MatchedView
        client={client}
        latch={latch}
        meId={meId}
        leftLatch={leftLatch}
        onLeft={setLeftLatch}
        onRefresh={refreshRef}
        showToast={showToast}
        matchedAt={matchedAtRef.current}
        onOpenSafety={() =>
          setSafety({ latchId: latch.id, participants: latch.participants ?? [], meId })
        }
        onOpenCancelDialog={openCancelDialog}
      />
    );
  } else if (latch && view === "closed") {
    inner = latch.my_response === "defer"
      ? (
        // 自分の見送り=04のdeclined状態(理由chipsは表示のみ・回答時のみ受理)。
        // 取り消し(undo)で未回答へ戻せる(D-06)
        <section className="latch-detail latch-declined">
          <DeclinedCard
            mode="confirmed"
            declineReasons={declineReasons}
            declineSending={declineSending}
            respondBusy={respondBusy}
            onToggleReason={(value) => {
              setDeclineReasons((prev) => {
                const next = new Set(prev);
                if (next.has(value)) next.delete(value);
                else next.add(value);
                return next;
              });
            }}
            onConfirmDecline={confirmDecline}
            onCancelDecline={cancelDecline}
            onUndo={undo}
          />
        </section>
      )
      : (
        // 二値化はview.jsの純関数に一元化(自分の操作履歴/統一文言・03 §7)。
        // 終了は一文表示+ホーム導線のv2カード(UI v2 Phase 5・gap分析§5-6)
        <section className="latch-detail latch-closed">
          <div className="closed-card">
            <p className="section-kicker"><span></span>LATCH</p>
            <p className="closed-text">{closedText(latch)}</p>
            <a className="ghost sb-again closed-home" href="#/">{HOME_LINK_TEXT}</a>
          </div>
        </section>
      );
  }

  return (
    <section id="detailScreen" className={className}>
      {inner}
      {error ? (
        <p className="detail-error" role="alert">{error}</p>
      ) : null}
      {/* 統合dialog(D-09)。旧index.htmlは空の<dialog>を常在させていたため
          閉じているあいだも要素だけは置く(開閉はopenで制御) */}
      <SafetyDialog
        client={client}
        open={safety != null}
        onClose={() => setSafety(null)}
        onBlocked={() => refreshRef.current?.()}
        latchId={safety?.latchId ?? latch?.id ?? null}
        participants={safety?.participants ?? null}
        meId={safety?.meId ?? meId}
      />
    </section>
  );
};

// -- 04 提案姿(プロトタイプ04が正源) --------------------------------------------

// 回答期限タイマー(分:秒・1秒更新・残5分でdng色)
const DeadlineTimer = ({ deadline, onExpire }) => {
  const [ms, setMs] = useState(() => remainingMs(deadline, new Date().toISOString()));
  const onExpireRef = useRef(onExpire);
  onExpireRef.current = onExpire;
  useEffect(() => {
    const update = () => {
      const value = remainingMs(deadline, new Date().toISOString());
      setMs(value);
      if (value <= 0) onExpireRef.current(); // 残時間0で回答ボタンをdisabled(送信可否の真実はサーバの409)
    };
    update();
    const timer = setInterval(update, DEADLINE_TICK_MS);
    return () => clearInterval(timer);
  }, [deadline]);
  return (
    <span
      role="timer"
      aria-labelledby="h-decide"
      className={`pv-timer${ms <= TIMER_LOW_MS ? " timer-low" : ""}`}
      data-role="deadline"
    >
      {countdownText(ms)}
    </span>
  );
};

// オーブ(04・acc色版)
const ProposalOrb = ({ latch }) => {
  const p = latch.proposal ?? {};
  const time = proposalTimeParts(p.time_summary);
  const chips = [
    { pos: "top:9%;left:0", mono: true, text: time?.clock ?? "—" },
    { pos: "top:16%;right:0", mono: false, text: p.area_name ?? "—" },
    { pos: "bottom:15%;left:2%", mono: false, text: p.headcount != null ? `${p.headcount}人` : "—" },
    { pos: "bottom:7%;right:4%", mono: true, text: p.budget?.max != null ? formatYen(p.budget.max) : "—" },
  ];
  return (
    <>
      <div className="pv-orb-haze" aria-hidden="true"></div>
      <div className="orb-ring" aria-hidden="true"></div>
      <div className="pv-orb-ring2" aria-hidden="true"></div>
      <div className="pv-orb-core" aria-hidden="true"></div>
      <div className="pripple" aria-hidden="true"></div>
      <div className="pripple d2" aria-hidden="true"></div>
      <div className="pripple d3" aria-hidden="true"></div>
      <svg viewBox="0 0 200 200" fill="none" className="orb-svg" aria-hidden="true">
        <circle cx="100" cy="100" r="74" style={{ stroke: "var(--acc-line)" }} strokeWidth="1" strokeDasharray="1.5 7"></circle>
        <g className="parcL"><path d="M100 70 A30 30 0 0 0 100 130" style={{ stroke: "var(--acc)" }} strokeWidth="7" strokeLinecap="round"></path></g>
        <g className="parcR"><path d="M100 70 A30 30 0 0 1 100 130" style={{ stroke: "var(--t1)" }} strokeWidth="7" strokeLinecap="round"></path></g>
      </svg>
      {chips.map(({ pos, mono, text }, i) => (
        <span key={i} className="orb-chip bright" data-mono={mono ? "" : undefined} style={posStyle(pos)}>
          {text}
        </span>
      ))}
    </>
  );
};

const posStyle = (pos) =>
  Object.fromEntries(
    pos.split(";").map((pair) => {
      const eq = pair.indexOf(":");
      return [pair.slice(0, eq), pair.slice(eq + 1)];
    }),
  );

// 4情報カード(全フィールド版のみ)
const InfoCard = ({ label, value, mono = false, sub }) => (
  <div className="pv-card">
    <span className="pv-card-label">{label}</span>
    <span className={`pv-card-value${mono ? " mono" : ""}`}>{value}</span>
    <span className="pv-card-sub">{sub}</span>
  </div>
);

const infoCards = (latch) => {
  const p = latch.proposal ?? {};
  const time = proposalTimeParts(p.time_summary);
  const headcount = p.headcount ?? 2;
  return (
    <>
      <InfoCard
        label="開始"
        value={time?.clock ?? "—"}
        mono
        sub={time?.date ?? "成立後にわかります"}
      />
      <InfoCard
        label="エリア"
        value={p.area_name ?? "—"}
        sub={p.area_name ? "お店は成立後に相談" : "成立後にわかります"}
      />
      <InfoCard label="人数" value={`${headcount}人`} sub={`あなた＋${headcount - 1}人`} />
      <InfoCard
        label="予算"
        value={p.budget?.max != null ? formatYen(p.budget.max) : "—"}
        mono={p.budget?.max != null}
        sub={p.budget?.max != null ? "1人あたり · 前後" : "成立後にわかります"}
      />
    </>
  );
};

// 照合表(自分の条件×候補の対比較)
const FitTable = ({ myIntent, latch, nowIso }) => {
  const rows = fitRows(myIntent, latch, nowIso);
  const allOk = myIntent != null;
  return (
    <>
      <div className="pv-fit-head">
        <h2 id="h-fit" className="card-title">{FIT_TITLE}</h2>
        <span className="pv-fit-sum">
          {allOk ? (
            <><Icon name="check" size={14} />{FIT_ALL_TEXT}</>
          ) : (
            FIT_UNAVAILABLE_TEXT
          )}
        </span>
      </div>
      <p className="vh">左があなたの条件、右がこの候補の内容です。</p>
      <ul className="pv-fit-list">
        {rows.map((row) => (
          <li key={row.label} className="fit-row">
            <span className="fit-label">{row.label}</span>
            <span className="fit-mine"><span className="vh">あなたの条件：</span>{row.mine}</span>
            <Icon name="arrowRight" size={16} />
            <span className="fit-theirs"><span className="vh">候補：</span>{row.theirs}</span>
            <span className="fit-ok" hidden={!row.ok}><Icon name="check" size={14} /></span>
          </li>
        ))}
      </ul>
      <p className="pv-fit-note">{FIT_NOTE_TEXT}</p>
    </>
  );
};

// 目隠しアバタースタック(自分=イニシャル・相手=点線円+目隠し)
const Avatars = ({ latch, meInitial }) => {
  const total = latch.proposal?.headcount ?? 2;
  const state = proposalStateOf(latch);
  const ring = state === "joined" || state === "allIn" ? "var(--acc)" : "var(--line-strong)";
  const blinds = Math.max(0, total - 1);
  return (
    <div aria-hidden="true" className="pv-avatar-stack">
      <span className="pv-avatar me" style={{ borderColor: ring }}>{meInitial}</span>
      {Array.from({ length: blinds }, (_, i) => (
        <span key={i} className="pv-avatar blind"><Icon name="eyeOff" size={18} /></span>
      ))}
    </div>
  );
};

// 4状態の回答カード(見送り理由chips=D-07)
const DeclinedCard = ({
  mode, // "pending" | "confirmed"
  declineReasons,
  declineSending,
  respondBusy,
  onToggleReason,
  onConfirmDecline,
  onCancelDecline,
  onUndo,
}) => (
  <div className="pv-state-card declined">
    <p className="pv-state-title">{DECLINED_TITLE}</p>
    <p className="pv-state-body">{DECLINED_NOTE_TEXT}</p>
    <div role="group" aria-labelledby="reason-label" className="pv-reasons">
      <span id="reason-label" className="pv-reason-label">
        {REASON_LABEL_TEXT}
        <span className="pv-reason-sub">{REASON_NOTE_TEXT}</span>
      </span>
      <div className="pv-reason-chips">
        {REASON_CHIPS.map(([value, label]) => {
          const on = declineReasons.has(value);
          return (
            <button
              key={value}
              type="button"
              className="ghost reason-chip"
              data-reason={value}
              aria-pressed={String(on)}
              onClick={() => onToggleReason(value)}
            >
              {on ? <Icon name="check" size={14} /> : null}
              {label}
            </button>
          );
        })}
      </div>
    </div>
    <div className="pv-state-actions">
      {mode === "pending" ? (
        <button
          type="button"
          className="pv-solid-link"
          data-role="confirm-decline"
          disabled={declineSending}
          onClick={onConfirmDecline}
        >
          {BACK_TO_STANDBY_LABEL}
        </button>
      ) : (
        <a className="pv-solid-link" href="#/standby">{BACK_TO_STANDBY_LABEL}</a>
      )}
      <button
        type="button"
        className="ghost"
        data-role={mode === "pending" ? "cancel-decline" : "undo"}
        disabled={respondBusy}
        onClick={mode === "pending" ? onCancelDecline : onUndo}
      >
        {UNDO_DECLINED_LABEL}
      </button>
    </div>
  </div>
);

const ProposalView = ({
  latch,
  myIntent,
  meInitial,
  declinePending,
  declineReasons,
  respondBusy,
  declineSending,
  deadlineExpired,
  onExpire,
  onRespond,
  onToggleReason,
  onConfirmDecline,
  onCancelDecline,
  onUndo,
  onOpenChat,
  onOpenSafety,
}) => {
  const minimal = isMinimalProposal(latch);
  const nowIso = new Date().toISOString();
  const state = proposalStateOf(latch);
  const reportEntry = !latch.is_group ? "この提案を通報する" : null; // グループ提案: 参加者非開示のため導線なし

  const states = (() => {
    if (declinePending && state === "open") {
      // 見送り未送信(理由chipsを選んでから離脱時に送る)
      return (
        <DeclinedCard
          mode="pending"
          declineReasons={declineReasons}
          declineSending={declineSending}
          respondBusy={respondBusy}
          onToggleReason={onToggleReason}
          onConfirmDecline={onConfirmDecline}
          onCancelDecline={onCancelDecline}
          onUndo={onUndo}
        />
      );
    }
    if (state === "open") {
      return (
        <div className="pv-state">
          {RESPONSE_BUTTONS.map(([value, label]) =>
            value === "yes" ? (
              <button
                key={value}
                type="button"
                className="cta pv-join"
                data-role="respond"
                data-value={value}
                disabled={respondBusy || deadlineExpired}
                onClick={() => onRespond(value)}
              >
                <Icon name="latchMark" size={24} />
                {label}
              </button>
            ) : (
              <button
                key={value}
                type="button"
                className="ghost pv-defer"
                data-role="respond"
                data-value={value}
                disabled={respondBusy || deadlineExpired}
                onClick={() => onRespond(value)}
              >
                {label}
              </button>
            ),
          )}
          <p className="pv-note">{RESPONSE_NOTE_TEXT}</p>
        </div>
      );
    }
    if (state === "joined") {
      return (
        <div className="pv-state-card joined">
          <p className="pv-state-title"><Icon name="check" size={22} />{JOINED_TITLE}</p>
          <p className="pv-state-body">{joinedWaitText(latch)}</p>
          <p className="pv-state-note">{JOINED_WAIT_NOTE}</p>
          <div className="pv-state-actions">
            <a className="pv-solid-link" href="#/standby">{BACK_TO_STANDBY_LABEL}</a>
            <button type="button" className="ghost" data-role="undo" disabled={respondBusy} onClick={onUndo}>
              {UNDO_JOINED_LABEL}
            </button>
          </div>
        </div>
      );
    }
    if (state === "allIn") {
      const total = latch.proposal?.headcount ?? 2;
      return (
        <div className="pv-state-card allin">
          <p className="pv-state-title">{total > 2 ? `${total}人` : ""}{ALL_IN_TITLE}</p>
          <p className="pv-state-body">{ALL_IN_LEAD_TEXT}</p>
          <button type="button" className="cta pv-chat" data-role="open-chat" onClick={onOpenChat}>
            {OPEN_CHAT_LABEL}
            <Icon name="arrowRight" size={18} />
          </button>
        </div>
      );
    }
    // declined(サーバ記録済み=my_response=defer)
    return (
      <DeclinedCard
        mode="confirmed"
        declineReasons={declineReasons}
        declineSending={declineSending}
        respondBusy={respondBusy}
        onToggleReason={onToggleReason}
        onConfirmDecline={onConfirmDecline}
        onCancelDecline={onCancelDecline}
        onUndo={onUndo}
      />
    );
  })();

  return (
    <section className="pv-detail" aria-labelledby="h-proposal">
      <div className="haze haze-proposal" aria-hidden="true"></div>
      <div className="pv-grid">
        {minimal ? null : (
          <div aria-hidden="true" className="pv-visual" data-role="pv-orb">
            <ProposalOrb latch={latch} />
          </div>
        )}
        <div className={`pv-text${minimal ? " pv-text--solo" : ""}`}>
          <p className="pv-badge rise"><span aria-hidden="true" className="pv-badge-dot"></span>LATCH候補 · {clockText(latch.created_at)} に届きました</p>
          <h1 id="h-proposal" className="pv-title rise r2">条件が、<br />揃いそうです。</h1>
          <p className="pv-lead rise r3">{PROPOSAL_LEAD_TEXT}</p>
          {minimal ? (
            <div className="pv-minimal rise r3">
              <Icon name="info" size={18} />
              <span>{HIDDEN_PROPOSAL_TEXT} — {MINIMAL_PROPOSAL_NOTE}</span>
            </div>
          ) : (
            <div className="pv-cards rise r3">{infoCards(latch)}</div>
          )}
        </div>
        {minimal ? null : (
          <section aria-labelledby="h-fit" className="pv-fit card-pad" data-role="pv-fit">
            <FitTable myIntent={myIntent} latch={latch} nowIso={nowIso} />
          </section>
        )}
        <section aria-labelledby="h-decide" className="pv-decide card-pad">
          <div className="pv-decide-head">
            <div className="pv-decide-timer">
              <h2 id="h-decide" className="card-title">回答期限まで</h2>
              <DeadlineTimer deadline={latch.response_deadline} onExpire={onExpire} />
              <span className="pv-deadline-note">{clockText(latch.response_deadline)}{DEADLINE_LINE_SUFFIX}</span>
            </div>
            <div className="pv-avatars">
              <Avatars latch={latch} meInitial={meInitial} />
              <span className="pv-who" data-role="who">{whoLineText(proposalStateOf(latch), latch)}</span>
            </div>
          </div>
          <div aria-live="polite" className="pv-states" data-role="pv-states">
            {states}
          </div>
        </section>
      </div>
      {reportEntry ? (
        <button type="button" className="report-entry" data-role="report-entry" onClick={onOpenSafety}>
          {reportEntry}
        </button>
      ) : null}
    </section>
  );
};

// -- 05 成立姿(プロトタイプ05が正源) --------------------------------------------

const LatchedHero = ({ latch, leftLatch, matchedAt }) => {
  const lead = leftLatch
    ? LEFT_LEAD_TEXT
    : `${latch.proposal?.headcount ?? 2}人全員が「参加する」と答えました。ここからは、お互いの姿が見えます。`;
  return (
    <>
      <div aria-hidden="true" className="lt-mark">
        <div className="lt-burst"></div>
        <div className="lt-mark-glow"></div>
        <svg viewBox="0 0 100 100" fill="none" className="lt-mark-svg">
          <g className="snapL"><path d="M50 22 A28 28 0 0 0 50 78" style={{ stroke: "var(--acc)" }} strokeWidth="7" strokeLinecap="round"></path></g>
          <g className="snapR"><path d="M50 22 A28 28 0 0 1 50 78" style={{ stroke: "var(--t1)" }} strokeWidth="7" strokeLinecap="round"></path></g>
        </svg>
      </div>
      <div className="lt-hero-text">
        <p className="lt-hero-kicker">成立{matchedAt ? ` · ${clockText(matchedAt)}` : ""}</p>
        <h1 id="h-latched" className="lt-hero-title">{leftLatch ? LEFT_TITLE : LATCHED_TITLE}</h1>
        <p className="lt-hero-lead">{lead}</p>
      </div>
      <div className="lt-hero-tags" aria-hidden="true">
        {latchedTags(latch).map((tag, i) => (
          <span key={i} className={`lt-tag${tag.mono ? " mono" : ""}`}>{tag.text}</span>
        ))}
      </div>
    </>
  );
};

// aside 1: 成立した内容(details+dl+カレンダーに追加=D-10)
const LatchedDetailsCard = ({ latch }) => {
  const wide = window.matchMedia?.("(min-width: 960px)")?.matches ?? true;
  const hasTime = hasCalendarTime(latch);
  return (
    <details className="lt-card lt-details" open={wide}>
      <summary>
        <h2 className="lt-card-title">{LATCHED_DETAILS_TITLE}</h2>
        <Icon name="chevronDown" size={18} className="chev" />
      </summary>
      <div className="lt-details-body">
        <dl className="lt-dl">
          {latchedRows(latch).map((row, i) => (
            <Fragment key={i}>
              <dt>{row.label}</dt>
              <dd>
                {row.value}
                {row.sub ? <span className="lt-dd-sub">{LATCHED_PLACE_SUB_TEXT}</span> : null}
              </dd>
            </Fragment>
          ))}
        </dl>
        <button type="button" className="ghost lt-cal" data-role="calendar-add" disabled={!hasTime} onClick={() => downloadIcs(latch)}>
          <Icon name="calendar" size={16} />
          カレンダーに追加
        </button>
      </div>
    </details>
  );
};

// aside 2: メンバー(色付きイニシャルアバター・自分=末尾にあなた行)
const MembersCard = ({ latch, meId, leftLatch }) => (
  <section className="lt-card" aria-labelledby="h-members">
    <h2 id="h-members" className="lt-card-title">メンバー</h2>
    <ul className="lt-members">
      {chatMembers(latch, meId).map((m, i) => (
        <li key={m.id ?? i}>
          <span
            aria-hidden="true"
            className={`lt-avatar lt-avatar--member${m.me ? " me" : ""}`}
            style={m.me ? undefined : { background: m.color }}
          >
            {m.initial}
          </span>
          <span className="lt-member-name">{m.me ? "あなた" : m.name}</span>
          {m.me && leftLatch ? (
            <span className="lt-member-status left">{MEMBER_LEFT_TEXT}</span>
          ) : (
            <span className="lt-member-status"><Icon name="check" size={13} />{MEMBER_JOINED_TEXT}</span>
          )}
        </li>
      ))}
    </ul>
    <p className="lt-member-note">{MEMBER_NOTE_TEXT}</p>
  </section>
);

// aside 3: 安全のために(安全ガイド+通報・ブロック=D-09統合dialog)
const SafetyCard = ({ onOpen }) => (
  <section className="lt-card" aria-labelledby="h-safety">
    <h2 id="h-safety" className="lt-card-title"><Icon name="shield" size={16} />{SAFETY_HEADING_TEXT}</h2>
    <details className="lt-guide">
      <summary>{SAFETY_GUIDE_TITLE}<Icon name="chevronDown" size={16} className="chev" /></summary>
      <ul>{SAFETY_GUIDE_ITEMS.map((item, i) => <li key={i}>{item}</li>)}</ul>
    </details>
    <button type="button" className="ghost danger lt-safety" aria-haspopup="dialog" data-role="safety-entry" onClick={onOpen}>
      <Icon name="flag" size={16} />
      {SAFETY_ENTRY_TEXT}
    </button>
  </section>
);

const MatchedView = ({
  client,
  latch,
  meId,
  matchedAt, // 成立時刻(yes直後のみ実時刻。APIにmatched_atはない)
  leftLatch,
  onLeft,
  onRefresh,
  showToast,
  onOpenSafety,
  onOpenCancelDialog,
}) => {
  // 参加持取り消し(D-08): 成功でleftLatchを立てて再取得で取り消し後姿へ
  const confirmLeave = async () => {
    try {
      await client.call("POST", `/v1/latches/${latch.id}/leave`, { body: {} });
    } catch (err) {
      if (err?.status === 409 && err?.code === "ALREADY_LEFT") {
        onLeft(true); // 取り消し済み — 取り消し後の姿へ収束させる
        await onRefresh.current?.();
        return;
      }
      showToast(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : "取り消せませんでした。");
      return;
    }
    onLeft(true);
    await onRefresh.current?.();
  };

  return (
    <section className="lt-detail" aria-labelledby="h-latched">
      <div className="haze haze-latched" aria-hidden="true"></div>
      <section className="lt-hero">
        <LatchedHero latch={latch} leftLatch={leftLatch} matchedAt={matchedAt} />
      </section>
      <div className="lt-grid">
        <Chat client={client} latch={latch} meId={meId} left={leftLatch} />
        <aside aria-label="成立の詳細" className="lt-aside">
          <LatchedDetailsCard latch={latch} />
          <MembersCard latch={latch} meId={meId} leftLatch={leftLatch} />
          {latch.status === "completed" ? (
            // 表示条件はstatusのみ(詳細応答のcompleted_atは常にnull・§9-6①。
            // 3日窓・二重回答はサーバの409で切替える)
            <AttendanceCard client={client} latchId={latch.id} />
          ) : null}
          <SafetyCard onOpen={onOpenSafety} />
          {leftLatch ? null : (
            <button
              type="button"
              className="danger lt-cancel"
              aria-haspopup="dialog"
              data-role="cancel-entry"
              onClick={onOpenCancelDialog}
            >
              {CANCEL_ENTRY_TEXT}
            </button>
          )}
        </aside>
      </div>
    </section>
  );
};
