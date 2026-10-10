// ブロック管理(M3 ws-8 design §2.6・UI v2 Phase 6でReact化)。旧blocks.js
// (素のDOM配線)をコンポーネントへ載せ替えたもの。設定画面の2セクションの一つ。
// 一覧+解除(確認dialog挟み — 再登録導線が成立済み詳細しかないため誤解除の
// 回復コストが高い)。解除の204で行除去(カーソル保持)・404(既に解除済み)は
// 通信エラー扱いにせず一覧を取り直して収束させる。遡及効果なしはUIで説明しない。
// 行とdialogはv2部品(sb-row系の行・dlg+safety系の操作ボタン)で構成する。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_SUFFIX,
  BLOCK_ERROR_TEXT,
  BLOCK_UNBLOCK_LABEL,
  BLOCKS_EMPTY_TEXT,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { noticeTimeText } from "./view.js";

export const BlocksSection = ({ client }) => {
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [pending, setPending] = useState(null); // 解除確認中の行
  const [errorText, setErrorText] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const dialogRef = useRef(null);

  const load = useCallback(async () => {
    const data = await client.call("GET", "/v1/users/me/blocks?limit=20");
    setItems(data.items);
    setCursor(data.next_cursor);
    setLoaded(true);
  }, [client]);

  useEffect(() => {
    load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
  }, [load]);

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/users/me/blocks?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    setItems((prev) => prev.concat(data.items)); // 追頁は累積(notice・listsと同型)
    setCursor(data.next_cursor);
  };

  // 確認dialog(<dialog class="dlg">・safety系の操作ボタンを踏襲)
  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (pending && !el.open) el.showModal();
  }, [pending]);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const onClosed = () => setPending(null);
    el.addEventListener("close", onClosed);
    return () => el.removeEventListener("close", onClosed);
  }, []);

  const close = () => {
    if (dialogRef.current?.open) dialogRef.current.close();
  };

  // backdropクリックで閉じる(Escは<dialog>標準)
  const onBackdropClick = (event) => {
    if (event.target === dialogRef.current && dialogRef.current.open) close();
  };

  const submit = async () => {
    const target = pending;
    if (!target) return;
    try {
      await client.call("DELETE", `/v1/users/${target.blocked_id}/block`);
      setItems((prev) => prev.filter((item) => item.blocked_id !== target.blocked_id));
      close(); // 行単位の除去(カーソル保持)
    } catch (err) {
      if (err?.status === 404) {
        close(); // 既に解除済み — 一覧を取り直して収束
        load().catch(() => {});
        return;
      }
      setErrorText(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : BLOCK_ERROR_TEXT);
    }
  };

  return (
    <div>
      <div className="block-list" id="blockList">
        {loaded && items.length > 0 ? (
          <ul className="block-rows">
            {items.map((item) => (
              <li key={item.blocked_id} className="block-row">
                <div className="sb-row-body">
                  <span className="block-name">{item.display_name}</span>
                  <span className="sb-row-meta">{noticeTimeText(item.created_at)} にブロック</span>
                </div>
                <button
                  type="button"
                  className="ghost danger block-unblock"
                  aria-haspopup="dialog"
                  aria-label={`${item.display_name}さんのブロックを解除`}
                  onClick={() => {
                    setErrorText(null);
                    setPending(item);
                  }}
                >
                  {BLOCK_UNBLOCK_LABEL}
                </button>
              </li>
            ))}
          </ul>
        ) : loaded ? (
          <p className="latch-empty"><strong>{BLOCKS_EMPTY_TEXT}</strong></p>
        ) : null}
      </div>
      <button className="ghost notice-more" type="button" hidden={cursor == null} onClick={() => loadMore().catch(() => {})}>
        もっと見る
      </button>

      {/* 解除の確認dialog(UI v2 Phase 5・旧modal-backdropから統一) */}
      <dialog className="dlg" id="unblockDialog" aria-labelledby="unblockTitle" ref={dialogRef} onClick={onBackdropClick}>
        {pending ? (
          <div className="dlg-col">
            <h2 className="dlg-title" id="unblockTitle">ブロックの解除</h2>
            <p className="dlg-desc"><strong>{pending.display_name}</strong>{BLOCK_CONFIRM_SUFFIX}</p>
            <p className="safety-error" role="alert" hidden={!errorText}>{errorText}</p>
            <div className="safety-actions">
              <button type="button" className="ghost safety-btn" onClick={close}>
                {BLOCK_CANCEL_LABEL}
              </button>
              <button type="button" className="safety-btn safety-solid" onClick={submit}>
                {BLOCK_UNBLOCK_LABEL}
              </button>
            </div>
          </div>
        ) : null}
      </dialog>
    </div>
  );
};
