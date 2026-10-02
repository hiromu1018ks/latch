// お知らせ(M3 ws-8 design §2.1〜§2.2)。topbarベルpopoverの中身。
// popoverを開くたびGET /v1/notifications?limit=20を1回取り直し(開いた時点の
// 最新)・表示された未読行へPOST read(開いた=見た)・起動時1回のpreload結果を
// 初回描画に使い二重取得しない。取得失敗時は前回表示を維持・空状態も出さない
// (再訪で再試行・detailの再取得失敗と同型)。
import { NOTICE_EMPTY_NOTE, NOTICE_EMPTY_TITLE } from "./texts.js";
import {
  escapeHtml,
  notificationHref,
  notificationLines,
  noticeTimeText,
} from "./view.js";

const noticeRowHtml = (item, nowIso) => {
  const lines = notificationLines(item, nowIso)
    .map((line) => `<span class="notice-line">${escapeHtml(line)}</span>`)
    .join("");
  const time = `<span class="notice-time">${escapeHtml(
    noticeTimeText(item.created_at),
  )}</span>`;
  const cls = `notice-item${item.read_at == null ? " unread" : ""}`;
  const href = notificationHref(item);
  return href
    ? `<a class="${cls}" href="${href}">${lines}${time}</a>`
    : `<div class="${cls}">${lines}${time}</div>`; // タップ先なし(nearby等)
};

export const createNotice = ({ client, el }) => {
  let items = [];
  let cursor = null;
  let preloaded = false;

  const hasUnread = () => items.some((item) => item.read_at == null);
  const updateDot = () => {
    el.dot.hidden = !hasUnread();
  };

  const render = () => {
    const nowIso = new Date().toISOString();
    el.list.innerHTML = items.length
      ? items.map((item) => noticeRowHtml(item, nowIso)).join("")
      : `<p class="notice-empty"><strong>${NOTICE_EMPTY_TITLE}</strong><span>${NOTICE_EMPTY_NOTE}</span></p>`;
    el.moreButton.hidden = cursor == null; // cursorは不透明文字列
  };

  const markRead = async () => {
    const unread = items.filter((item) => item.read_at == null);
    await Promise.allSettled(
      unread.map((item) =>
        client
          .call("POST", `/v1/notifications/${item.id}/read`)
          .then(() => {
            item.read_at = new Date().toISOString();
          }),
      ),
    );
    updateDot(); // 失敗した行は次回開いた時の未読対象のまま
  };

  const preload = async () => {
    const data = await client.call("GET", "/v1/notifications?limit=20");
    items = data.items;
    cursor = data.next_cursor;
    preloaded = true;
    render();
    updateDot();
  };

  const open = async () => {
    if (preloaded) {
      preloaded = false; // 起動時取得の結果を初回描画に使い二重取得しない
      render();
    } else {
      try {
        const data = await client.call("GET", "/v1/notifications?limit=20");
        items = data.items;
        cursor = data.next_cursor;
        render();
      } catch {
        return; // 前回表示を維持(空状態も出さない)
      }
    }
    await markRead();
  };

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/notifications?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    items = items.concat(data.items);
    cursor = data.next_cursor;
    render();
  };

  return { preload, open, loadMore };
};
