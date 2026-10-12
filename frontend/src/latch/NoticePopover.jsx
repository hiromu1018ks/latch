// お知らせpopover(M3 ws-8 design §2.1〜§2.2・UI v2 Phase 5でv2部品へ再構成・
// UI v2 Phase 6でReact化)。旧notice.js(素のDOM配線)をhook+コンポーネントへ
// 載せ替えたもの。popoverを開くたびGET /v1/notifications?limit=20を1回取り直し
// (開いた時点の最新=起動後に届いた通知も初回オープンで描画する。findings#2
// 修正でpreloaded分岐を削除・2026-10-12)・表示された未読行へPOST read
// (開いた=見た)。起動時1回のpreloadはヘッダーの未読ドット判定だけに使う
// (popoverの行描画には使わない)。取得失敗時は前回表示を維持・空状態も出さ
// ない(再訪で再試行・detailの再取得失敗と同型)。行は未読にaccの点と
// 「未読」の視覚隠し補読を添える。03のアプリ内トースト(候補の即時導線)とこの
// 一覧(履歴)はD-14の判断で併存(統合の判断はphase5-report.mdに記録)。
import { useCallback, useRef, useState } from "react";
import { NOTICE_EMPTY_NOTE, NOTICE_EMPTY_TITLE } from "./texts.js";
import {
  notificationHref,
  notificationLines,
  noticeTimeText,
} from "./view.js";

// お知らせデータの取得・既読化(ヘッダーのドットとpopover行で共有する)
export const useNotice = (client) => {
  const [items, setItems] = useState([]);
  const itemsRef = useRef([]); // 既読化(非同期)から触るミラー
  const [cursor, setCursor] = useState(null);
  const [fetched, setFetched] = useState(false); // 空状態は1回以上取得した後だけ出す
  const [hasUnread, setHasUnread] = useState(false);

  const updateDot = (list) => setHasUnread(list.some((item) => item.read_at == null));

  const apply = (data) => {
    itemsRef.current = data.items;
    setItems(data.items);
    setCursor(data.next_cursor); // cursorは不透明文字列
    setFetched(true);
    updateDot(data.items);
  };

  const preload = useCallback(async () => {
    // 起動時1回: ヘッダーの未読ドット判定のための取得(popoverの行描画には
    // 使わない — open()が常に最新を取り直すため二重保持しない)
    const data = await client.call("GET", "/v1/notifications?limit=20");
    apply(data);
  }, [client]);

  const markRead = useCallback(async () => {
    // 未読行へだけPOST read(既読行には呼ばない)
    const unread = itemsRef.current.filter((item) => item.read_at == null);
    await Promise.allSettled(
      unread.map((item) =>
        client
          .call("POST", `/v1/notifications/${item.id}/read`)
          .then(() => {
            item.read_at = new Date().toISOString();
          }),
      ),
    );
    // 失敗した行は次回開いた時の未読対象のまま(ドットを再判定)
    setItems([...itemsRef.current]);
    updateDot(itemsRef.current);
  }, [client]);

  const open = useCallback(async () => {
    // 開くたびに必ず取得する(findings#2修正: preloaded分岐は起動後に届いた
    // 通知を初回オープンで描画しない要因だった。Headerコメント「開くたびに
    // 取得」との整合を回復)
    try {
      const data = await client.call("GET", "/v1/notifications?limit=20");
      apply(data);
    } catch {
      return; // 前回表示を維持(空状態も出さない)
    }
    await markRead();
  }, [client, markRead]);

  const loadMore = useCallback(async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/notifications?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    setItems((prev) => prev.concat(data.items)); // 追頁は累積(blocks・listsと同型)
    setCursor(data.next_cursor);
  }, [client, cursor]);

  return { items, cursor, fetched, hasUnread, preload, open, loadMore };
};

// popoverの中身(行一覧+空状態+もっと見る)
export const NoticeList = ({ notice }) => {
  const { items, cursor, fetched, loadMore } = notice;
  const nowIso = new Date().toISOString();
  return (
    <>
      <div className="notice-list" id="noticeList">
        {fetched ? (
          items.length ? (
            items.map((item) => <NoticeRow key={item.id} item={item} nowIso={nowIso} />)
          ) : (
            <p className="notice-empty"><strong>{NOTICE_EMPTY_TITLE}</strong><span>{NOTICE_EMPTY_NOTE}</span></p>
          )
        ) : null}
      </div>
      <button className="ghost notice-more" id="noticeMoreButton" type="button" hidden={cursor == null} onClick={() => loadMore().catch(() => {})}>
        もっと見る
      </button>
    </>
  );
};

const NoticeRow = ({ item, nowIso }) => {
  const unread = item.read_at == null;
  const cls = `notice-item${unread ? " unread" : ""}`;
  const href = notificationHref(item);
  const body = (
    <>
      {unread ? (
        <>
          <span className="notice-dot" aria-hidden="true"></span>
          <span className="vh">未読</span>
        </>
      ) : null}
      <span className="notice-body">
        {notificationLines(item, nowIso).map((line, i) => (
          <span key={i} className="notice-line">{line}</span>
        ))}
      </span>
      <span className="notice-time">{noticeTimeText(item.created_at)}</span>
    </>
  );
  // タップ先なし(nearby等)はdiv・それ以外は行リンク
  return href ? (
    <a className={cls} href={href}>{body}</a>
  ) : (
    <div className={cls}>{body}</div>
  );
};
