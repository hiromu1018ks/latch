// 候補・成立の簡易一覧(UI v2 Phase 5・phase0-decisions D-11の最小版)。
// 旧lists.js(素のDOM配線)をReactコンポーネントへ載せ替えたもの(UI v2 Phase 6)。
// topnav/tabbarの「候補」「成立」タブの一覧画面。様式は03のリスト部品
// (sb-row: 44pxアイコン+原文+meta+pill)の踏襲で、行の選択で04/05詳細へ遷移する。
// データ源はGET /v1/latches?limit=20(statusはproposed/partial_accept=候補・
// matched/completed=成立に分類)。見出しは承認済みの通知文言
// (PROPOSAL_NOTICE_TITLE・MATCHED_NOTICE_TITLE)を流用する。
import { useEffect, useRef, useState } from "react";
import {
  HIDDEN_PROPOSAL_TEXT,
  LIST_MATCHED_BADGE,
  LIST_PROPOSALS_BADGE,
  MATCHED_NOTICE_TITLE,
  NEXT_ACTION_TEXT,
  NO_LATCH_NOTE,
  NO_LATCH_TITLE,
  NO_MATCHED_NOTE,
  NO_MATCHED_TITLE,
  PROPOSAL_NOTICE_TITLE,
  STATUS_TEXT,
} from "./texts.js";
import { isMinimalProposal, remainingTimeText } from "./view.js";
import { Icon } from "../ui/Icon.jsx";
import { useChrome } from "../ui/chrome.jsx";
import { categoryIconName, splitStatuses } from "./home.js";

const MODES = {
  proposals: {
    badge: LIST_PROPOSALS_BADGE,
    title: PROPOSAL_NOTICE_TITLE,
    empty: [NO_LATCH_TITLE, NO_LATCH_NOTE],
  },
  matched: {
    badge: LIST_MATCHED_BADGE,
    title: MATCHED_NOTICE_TITLE,
    empty: [NO_MATCHED_TITLE, NO_MATCHED_NOTE],
  },
};

// 一覧行(候補=原文または「条件が合う候補があります」+残時間・成立=内容+導線文言)
export const LatchRow = ({ latch, nowIso, first = false }) => {
  const minimal = isMinimalProposal(latch);
  const p = latch.proposal ?? {};
  const title = minimal
    ? HIDDEN_PROPOSAL_TEXT
    : [p.time_summary, p.area_name].filter(Boolean).join(" ");
  const isCandidate = ["proposed", "partial_accept"].includes(latch.status);
  const meta = [
    !minimal && p.headcount != null ? `${p.headcount}人` : null,
    isCandidate
      ? remainingTimeText(latch.response_deadline, nowIso) // 最小形も残時間は出す(03 §4の様式)
      : NEXT_ACTION_TEXT,
  ].filter(Boolean);
  const status = STATUS_TEXT[latch.status] ?? latch.status;
  // 候補=acc(navバッジと同じ強調)・完了=s2のgone様式
  const pillClass = latch.status === "completed" ? "gone" : "acc";
  return (
    <li className="sb-row" data-border={first ? undefined : "true"}>
      <span className="sb-row-ic" aria-hidden="true">
        <Icon name={categoryIconName(p.category_primary)} size={19} />
      </span>
      <a className="sb-row-body sb-row-link" href={`#/latches/${latch.id}`}>
        <span className="sb-row-text">{minimal ? title : `「${title}」`}</span>
        {meta.length ? <span className="sb-row-meta">{meta.join(" · ")}</span> : null}
      </a>
      <span className={`sb-pill ${pillClass}`}>{status}</span>
    </li>
  );
};

export const ListsScreen = ({ client, mode }) => {
  const { setCounts } = useChrome();
  const conf = MODES[mode];
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loaded, setLoaded] = useState(false);
  // 全status(バッジ集計用・splitStatusesに渡す)。追頁は累積(notice・blocksと同型)
  const latchItemsRef = useRef([]);

  const apply = (data) => {
    latchItemsRef.current = latchItemsRef.current.concat(data.items);
    setCursor(data.next_cursor);
    const { candidates, matched } = splitStatuses(latchItemsRef.current);
    setItems(mode === "proposals" ? candidates : matched);
    setCounts(
      mode === "proposals"
        ? { proposals: candidates.length }
        : { matched: matched.length },
    );
    setLoaded(true);
  };

  useEffect(() => {
    let alive = true;
    latchItemsRef.current = [];
    client
      .call("GET", "/v1/latches?limit=20")
      .then((data) => {
        if (alive) apply(data);
      })
      .catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, mode]);

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/latches?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    apply(data);
  };

  const nowIso = new Date().toISOString();

  // 旧実装はshow()の完了まで描画が走らなかったため、取得中は骨組みだけを置く
  if (!loaded) {
    return (
      <div className="page-main page-main--lists" id={mode === "proposals" ? "proposalsScreen" : "matchedScreen"}>
        <section className="list-hero">
          <p className="pv-badge" data-list-badge hidden><span className="pv-badge-dot" aria-hidden="true"></span><span data-list-badge-text>{conf.badge} · 0件</span></p>
          <h1 className="list-title" data-list-title></h1>
        </section>
        <section className="sb-others-sec">
          <ul className="sb-others" data-list-rows></ul>
          <button className="ghost notice-more" data-list-more type="button" hidden>もっと見る</button>
        </section>
      </div>
    );
  }

  return (
    <div className="page-main page-main--lists" id={mode === "proposals" ? "proposalsScreen" : "matchedScreen"}>
      <section className="list-hero">
        <p className="pv-badge" data-list-badge>
          <span className="pv-badge-dot" aria-hidden="true"></span>
          <span data-list-badge-text>{conf.badge} · {items.length}件</span>
        </p>
        <h1 className="list-title" data-list-title>{conf.title}</h1>
      </section>
      <section className="sb-others-sec">
        <ul className="sb-others" data-list-rows>
          {items.length
            ? items.map((latch, i) => (
                <LatchRow key={latch.id} latch={latch} nowIso={nowIso} first={i === 0} />
              ))
            : (
              <li className="latch-empty list-empty">
                <strong>{conf.empty[0]}</strong>
                <span>{conf.empty[1]}</span>
              </li>
            )}
        </ul>
        <button className="ghost notice-more" data-list-more type="button" hidden={cursor == null} onClick={() => loadMore().catch(() => {})}>
          もっと見る
        </button>
      </section>
    </div>
  );
};
