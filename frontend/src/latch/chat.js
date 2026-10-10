// チャット(05成立・チャット・プロトタイプ05が正源+M3 ws-7 design §2.6)。
// 構成は全面再構成(UI v2 Phase 4): ヘッダー(「今夜の3人」+条件+アバタースタック)・
// role="log"・開始システム行(acc pill)・相手=アバター+名前+時間+bubble・
// 自分=右bubble・システム行(sender_id NULL=参加取り消し告知・dng pill)・
// 単行input+円形送信ボタン・読み取り専用表示。
// 同期ロジックは現行踏襲: 30秒ポーリング+送信後即時再取得+visibilitychange。
// 初回はlimit=100でnext_cursorが尽きるまで(上限5頁=500件・超過時は先頭を省略)。
// 順序の真実はサーバ(ローカル追記しない)。
import {
  CHAT_COMPLETED_TEXT,
  CHAT_EMPTY_ERROR_TEXT,
  CHAT_LEFT_PLACEHOLDER_TEXT,
  CHAT_OPENING_TEXT,
  CHAT_PLACEHOLDER_TEXT,
  CHAT_SEND_ERROR_TEXT,
  CHAT_UNAVAILABLE_TEXT,
} from "./texts.js";
import { chatHeadText, chatMembers, clockText, escapeHtml } from "./view.js";
import { iconHtml } from "../ui/icons.js";

const POLL_MS = 30_000;
const PAGE_LIMIT = 100;
const MAX_PAGES = 5;

export const createChat = ({ client, latch, meId, mount }) => {
  let messages = [];
  let cursor = null;
  let timer = null;
  let left = false; // 参加持取り消し済み(D-08・leave成功時にmarkLeft)
  // matchedのみ書込可(それ以外は閲覧のみ・03 §6)
  let readonlyReason = latch.status === "matched" ? null : CHAT_COMPLETED_TEXT;

  const members = chatMembers(latch, meId);
  const head = chatHeadText(latch);
  const memberById = new Map(members.map((m) => [m.id, m]));

  // -- 骨格(ヘッダー+log+フォーム・プロトタイプ05の構成) ------------------------
  mount.classList.add("lt-chat");
  mount.innerHTML = `
    <div class="lt-chat-head">
      <div class="lt-chat-titlewrap">
        <h2 class="lt-chat-title"></h2>
        <span class="lt-chat-sub">${escapeHtml(head.sub)}</span>
      </div>
      <div aria-hidden="true" class="lt-avatar-stack">
        ${members
          .map(
            (m) =>
              `<span class="lt-avatar${m.me ? " me" : ""}" style="${
                m.me ? "" : `background:${m.color}`
              }">${escapeHtml(m.initial)}</span>`,
          )
          .join("")}
      </div>
    </div>
    <div id="chatlog" class="lt-log" role="log" aria-label="チャット" tabindex="0"></div>
    <p class="chat-notice" hidden></p>
    <form class="lt-chat-form">
      <label for="msg" class="vh">メッセージ</label>
      <input id="msg" class="field" type="text" autocomplete="off" enterkeyhint="send" maxlength="1000" placeholder="${escapeHtml(
        CHAT_PLACEHOLDER_TEXT,
      )}" />
      <button type="submit" class="cta lt-send" aria-label="送信" disabled>${iconHtml(
        "send",
        20,
      )}</button>
    </form>`;

  mount.querySelector(".lt-chat-title").textContent = head.title;
  const logEl = mount.querySelector(".lt-log");
  const noticeEl = mount.querySelector(".chat-notice");
  const inputEl = mount.querySelector("input");
  const formEl = mount.querySelector("form");
  const sendButton = mount.querySelector(".lt-send");

  const setSendDisabled = () => {
    sendButton.disabled = Boolean(readonlyReason) || left || !inputEl.value.trim();
  };

  inputEl.addEventListener("input", setSendDisabled);

  const setReadonly = (text) => {
    inputEl.disabled = true;
    sendButton.disabled = true;
    noticeEl.textContent = text;
    noticeEl.hidden = false;
  };

  // 参加持取り消し後(D-08): 書込不可+取り消し専用のplaceholder
  const markLeft = () => {
    left = true;
    inputEl.disabled = true;
    inputEl.placeholder = CHAT_LEFT_PLACEHOLDER_TEXT;
    sendButton.disabled = true;
  };

  const openingHtml = () => `
    <p class="msg lt-opening">
      <svg width="14" height="14" viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M20.5 5 A15 15 0 0 0 20.5 35" style="stroke: var(--acc)" stroke-width="4.5" stroke-linecap="round"></path><path d="M20.5 5 A15 15 0 0 1 20.5 35" style="stroke: var(--acc)" stroke-width="4.5" stroke-linecap="round"></path></svg>
      ${escapeHtml(CHAT_OPENING_TEXT)}
    </p>`;

  const rowHtml = (m) => {
    const time = clockText(m.created_at);
    if (m.sender_id === null) {
      // システム行(参加取り消しの告知・05 §5 leave)。文言はサーバのbodyのまま
      return `<p class="msg lt-system">${escapeHtml(m.body)}</p>`;
    }
    if (m.sender_id === meId) {
      return `
        <div class="msg lt-msg mine">
          <span class="lt-msg-meta">あなた · ${escapeHtml(time)}</span>
          <p class="lt-bubble">${escapeHtml(m.body)}</p>
        </div>`;
    }
    const member = memberById.get(m.sender_id);
    const name = member?.name ?? "";
    return `
      <div class="msg lt-msg theirs">
        <span aria-hidden="true" class="lt-avatar lt-avatar--msg" style="background:${
          member?.color ?? "var(--s3)"
        }">${escapeHtml(member?.initial ?? "?")}</span>
        <div class="lt-msg-body">
          <span class="lt-msg-meta">${escapeHtml(name)} · ${escapeHtml(time)}</span>
          <p class="lt-bubble">${escapeHtml(m.body)}</p>
        </div>
      </div>`;
  };

  const render = () => {
    // 保持(messages)は昇順の全量・「先頭を省略して表示」はここだけ(design §2.6)。
    // 保持側を切り詰めると差分同期のid排除と不整合を起こす(順序崩壊)。
    const visible = messages.length > MAX_PAGES * PAGE_LIMIT
      ? messages.slice(messages.length - MAX_PAGES * PAGE_LIMIT)
      : messages;
    logEl.innerHTML = `${openingHtml()}${visible.map(rowHtml).join("")}`;
    logEl.scrollTop = logEl.scrollHeight;
  };

  const sync = async () => {
    let pages = 0;
    // cursorなし=最古頁・next_cursorは「より新しい頁」への不透明文字列。
    // 初回はnext_cursorが尽きるまで(上限5頁)進めて会話の末尾(最新)まで。
    // 終端(next_cursor=null)に達したらcursorを「当該頁の取得に使った位置」へ
    // 戻す — 以降の差分同期はその頁を取り直し、既知はid排除・新着だけが
    // 追記される(design §2.6「保持している最終頁のcursorから差分を追記」)。
    const seen = new Set(messages.map((m) => m.id));
    do {
      const requestCursor = cursor;
      const query = requestCursor
        ? `?limit=${PAGE_LIMIT}&cursor=${encodeURIComponent(requestCursor)}`
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
      if (data.next_cursor === null) {
        cursor = requestCursor; // 次回の差分同期はこの頁の位置から
        break; // 終端 — これ以上進む頁はない
      }
      cursor = data.next_cursor;
      pages += 1;
    } while (pages < MAX_PAGES);
    render();
  };

  const send = async () => {
    if (inputEl.disabled) return;
    try {
      await client.call("POST", `/v1/latches/${latch.id}/messages`, {
        body: { body: inputEl.value }, // フロントではtrimしない(§2.6)
      });
      inputEl.value = "";
      setSendDisabled();
      await sync(); // 201でローカルに追記せず再取得
    } catch (err) {
      if (err?.code === "CHAT_READONLY") {
        // D-23: 理由はこれ以上表示しない(ブロック事実の推察を避ける)
        readonlyReason = CHAT_UNAVAILABLE_TEXT;
        setReadonly(CHAT_UNAVAILABLE_TEXT);
        return;
      }
      noticeEl.textContent =
        err?.code === "VALIDATION_ERROR"
          ? CHAT_EMPTY_ERROR_TEXT // 空白のみ等(§2.6)
          : CHAT_SEND_ERROR_TEXT;
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
    setSendDisabled();
    if (readonlyReason) setReadonly(readonlyReason);
    timer = setInterval(() => sync().catch(() => {}), POLL_MS);
    document.addEventListener("visibilitychange", onVisible);
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
    document.removeEventListener("visibilitychange", onVisible);
  };

  return { start, stop, send, markLeft };
};
