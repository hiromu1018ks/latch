// チャット(M3 ws-7 design §2.6)。30秒ポーリング+送信後即時再取得+
// visibilitychange。初回はlimit=100でnext_cursorが尽きるまで(上限5頁=500件・
// 超過時は先頭を省略)。順序の真実はサーバ(ローカル追記しない)。
import { CHAT_COMPLETED_TEXT, CHAT_UNAVAILABLE_TEXT } from "./texts.js";
import { escapeHtml, senderName } from "./view.js";

const POLL_MS = 30_000;
const PAGE_LIMIT = 100;
const MAX_PAGES = 5;

export const createChat = ({ client, latch, meId, mount }) => {
  let messages = [];
  let cursor = null;
  let timer = null;
  // matchedのみ書込可(それ以外は閲覧のみ・03 §6)
  const readonlyReason =
    latch.status === "matched" ? null : CHAT_COMPLETED_TEXT;

  const listEl = document.createElement("div");
  listEl.className = "chat-messages";
  listEl.setAttribute("aria-live", "polite");
  const noticeEl = document.createElement("p");
  noticeEl.className = "chat-notice";
  noticeEl.hidden = true;
  const formEl = document.createElement("form");
  formEl.className = "chat-form";
  const inputEl = document.createElement("textarea");
  inputEl.className = "chat-input";
  inputEl.maxLength = 1000; // trim後1〜1000字の上限(空白のみはサーバ422)
  inputEl.rows = 2;
  inputEl.setAttribute("aria-label", "メッセージ");
  const sendButton = document.createElement("button");
  sendButton.type = "submit";
  sendButton.className = "chat-send";
  sendButton.textContent = "送信";
  formEl.append(inputEl, sendButton);
  mount.replaceChildren(listEl, noticeEl, formEl);

  const setReadonly = (text) => {
    inputEl.disabled = true;
    sendButton.disabled = true;
    noticeEl.textContent = text;
    noticeEl.hidden = false;
  };

  const render = () => {
    listEl.replaceChildren(
      ...messages.map((m) => {
        const row = document.createElement("div");
        const mine = m.sender_id === meId;
        row.className = `chat-message ${mine ? "chat-mine" : "chat-theirs"}`;
        // 自分=右(名前なし)・相手=左(送信者名は詳細応答participantsから)
        const name = mine
          ? ""
          : `<span class="chat-sender">${escapeHtml(
              senderName(m.sender_id, latch.participants ?? []),
            )}</span>`;
        row.innerHTML = `${name}<span class="chat-body">${escapeHtml(m.body)}</span>`;
        return row;
      }),
    );
    listEl.scrollTop = listEl.scrollHeight;
  };

  const sync = async () => {
    let pages = 0;
    // cursorなし=最古頁・next_cursorは「より新しい頁」への不透明文字列。
    // 初回はnext_cursorが尽きるまで進めて会話の末尾(最新)まで取得する。
    // 終端(next_cursor=null)到達後の再同期も最古頁からになるため、追記は
    // id重複排除で行う(design §2.6「保持しているcursorから差分を追記」)。
    const seen = new Set(messages.map((m) => m.id));
    do {
      const query = cursor
        ? `?limit=${PAGE_LIMIT}&cursor=${encodeURIComponent(cursor)}`
        : `?limit=${PAGE_LIMIT}`;
      const data = await client.call(
        "GET",
        `/v1/latches/${latch.id}/messages${query}`,
      );
      for (const m of data.items) {
        if (!seen.has(m.id)) {
          messages.push(m);
          seen.add(m.id);
        }
      }
      cursor = data.next_cursor;
      pages += 1;
    } while (cursor && pages < MAX_PAGES);
    if (messages.length > MAX_PAGES * PAGE_LIMIT) {
      messages = messages.slice(messages.length - MAX_PAGES * PAGE_LIMIT);
    }
    render();
  };

  const send = async () => {
    if (inputEl.disabled) return;
    try {
      await client.call("POST", `/v1/latches/${latch.id}/messages`, {
        body: { body: inputEl.value }, // フロントではtrimしない(§2.6)
      });
      inputEl.value = "";
      await sync(); // 201でローカルに追記せず再取得
    } catch (err) {
      if (err?.code === "CHAT_READONLY") {
        // D-23: 理由はこれ以上表示しない(ブロック事実の推察を避ける)
        setReadonly(CHAT_UNAVAILABLE_TEXT);
        return;
      }
      noticeEl.textContent =
        err?.code === "VALIDATION_ERROR"
          ? "メッセージを入力してください。" // 空白のみ等(§2.6)
          : "送信できませんでした。";
      noticeEl.hidden = false;
    }
  };

  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });

  const onVisible = () => {
    if (document.visibilityState === "visible") sync().catch(() => {});
  };

  const start = async () => {
    await sync();
    if (readonlyReason) setReadonly(readonlyReason);
    timer = setInterval(() => sync().catch(() => {}), POLL_MS);
    document.addEventListener("visibilitychange", onVisible);
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
    document.removeEventListener("visibilitychange", onVisible);
  };

  return { start, stop, send };
};
