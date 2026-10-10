// チャット(05成立・チャット・プロトタイプ05が正源+M3 ws-7 design §2.6)。
// 旧chat.js(素のDOM配線)をReactコンポーネントへ載せ替えたもの(UI v2 Phase 6)。
// 構成: ヘッダー(「今夜の3人」+条件+アバタースタック)・role="log"・開始システム行
// (acc pill)・相手=アバター+名前+時間+bubble・自分=右bubble・システム行
// (sender_id NULL=参加取り消し告知・dng pill)・単行input+円形送信ボタン・
// 読み取り専用表示。同期ロジックは現行踏襲: 30秒ポーリング+送信後即時再取得+
// visibilitychange。初回はlimit=100でnext_cursorが尽きるまで(上限5頁=500件・
// 超過時は先頭を省略)。順序の真実はサーバ(ローカル追記しない)。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CHAT_COMPLETED_TEXT,
  CHAT_EMPTY_ERROR_TEXT,
  CHAT_LEFT_PLACEHOLDER_TEXT,
  CHAT_OPENING_TEXT,
  CHAT_PLACEHOLDER_TEXT,
  CHAT_SEND_ERROR_TEXT,
  CHAT_UNAVAILABLE_TEXT,
} from "./texts.js";
import { chatHeadText, chatMembers, clockText } from "./view.js";
import { Icon } from "../ui/Icon.jsx";

const POLL_MS = 30_000;
const PAGE_LIMIT = 100;
const MAX_PAGES = 5;

export const Chat = ({ client, latch, meId, left = false }) => {
  const members = chatMembers(latch, meId);
  const head = chatHeadText(latch);
  const memberById = new Map(members.map((m) => [m.id, m]));

  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [errorText, setErrorText] = useState(null);
  // matchedのみ書込可(それ以外は閲覧のみ・03 §6)
  const [readonlyText, setReadonlyText] = useState(
    latch.status === "matched" ? null : CHAT_COMPLETED_TEXT,
  );

  // 同期は非同期で走るため、保持(messages)はミラー経由で触る(昇順の全量。
  // 「先頭を省略して表示」は描画だけ・design §2.6)
  const messagesRef = useRef([]);
  const cursorRef = useRef(null);
  const logRef = useRef(null);

  const sync = useCallback(async () => {
    let pages = 0;
    // cursorなし=最古頁・next_cursorは「より新しい頁」への不透明文字列。
    // 初回はnext_cursorが尽きるまで(上限5頁)進めて会話の末尾(最新)まで。
    // 終端(next_cursor=null)に達したらcursorを「当該頁の取得に使った位置」へ
    // 戻す — 以降の差分同期はその頁を取り直し、既知はid排除・新着だけが
    // 追記される(design §2.6「保持している最終頁のcursorから差分を追記」)。
    const seen = new Set(messagesRef.current.map((m) => m.id));
    do {
      const requestCursor = cursorRef.current;
      const query = requestCursor
        ? `?limit=${PAGE_LIMIT}&cursor=${encodeURIComponent(requestCursor)}`
        : `?limit=${PAGE_LIMIT}`;
      const data = await client.call("GET", `/v1/latches/${latch.id}/messages${query}`);
      for (const m of data.items) {
        if (!seen.has(m.id)) {
          messagesRef.current = [...messagesRef.current, m];
          seen.add(m.id);
        }
      }
      if (data.next_cursor === null) {
        cursorRef.current = requestCursor; // 次回の差分同期はこの頁の位置から
        break; // 終端 — これ以上進む頁はない
      }
      cursorRef.current = data.next_cursor;
      pages += 1;
    } while (pages < MAX_PAGES);
    setMessages(messagesRef.current); // 参照更新で再描画(順序はサーバの真実)
  }, [client, latch.id]);

  // 30秒ポーリング+visibilitychange再取得(現行踏襲)
  useEffect(() => {
    sync().catch(() => {});
    const timer = setInterval(() => sync().catch(() => {}), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") sync().catch(() => {});
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sync]);

  // 新着で末尾へ(旧renderのlogEl.scrollTop=scrollHeightと同じ)
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages]);

  const inputDisabled = Boolean(readonlyText) || left;
  const sendDisabled = Boolean(readonlyText) || left || !inputValue.trim();

  const send = async () => {
    if (inputDisabled) return;
    try {
      await client.call("POST", `/v1/latches/${latch.id}/messages`, {
        body: { body: inputValue }, // フロントではtrimしない(§2.6)
      });
      setInputValue("");
      await sync(); // 201でローカルに追記せず再取得
    } catch (err) {
      if (err?.code === "CHAT_READONLY") {
        // D-23: 理由はこれ以上表示しない(ブロック事実の推察を避ける)
        setReadonlyText(CHAT_UNAVAILABLE_TEXT);
        return;
      }
      setErrorText(
        err?.code === "VALIDATION_ERROR"
          ? CHAT_EMPTY_ERROR_TEXT // 空白のみ等(§2.6)
          : CHAT_SEND_ERROR_TEXT,
      );
    }
  };

  // 保持(messages)は昇順の全量・「先頭を省略して表示」はここだけ(design §2.6)。
  // 保持側を切り詰めると差分同期のid排除と不整合を起こす(順序崩壊)。
  const visible =
    messages.length > MAX_PAGES * PAGE_LIMIT
      ? messages.slice(messages.length - MAX_PAGES * PAGE_LIMIT)
      : messages;

  return (
    <section className="lt-chat-slot lt-chat" aria-labelledby="h-chat">
      <div className="lt-chat-head">
        <div className="lt-chat-titlewrap">
          <h2 className="lt-chat-title">{head.title}</h2>
          <span className="lt-chat-sub">{head.sub}</span>
        </div>
        <div aria-hidden="true" className="lt-avatar-stack">
          {members.map((m) => (
            <span
              key={m.id}
              className={`lt-avatar${m.me ? " me" : ""}`}
              style={m.me ? undefined : { background: m.color }}
            >
              {m.initial}
            </span>
          ))}
        </div>
      </div>
      <div id="chatlog" className="lt-log" role="log" aria-label="チャット" tabIndex={0} ref={logRef}>
        <p className="msg lt-opening">
          <svg width="14" height="14" viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M20.5 5 A15 15 0 0 0 20.5 35" style={{ stroke: "var(--acc)" }} strokeWidth="4.5" strokeLinecap="round"></path><path d="M20.5 5 A15 15 0 0 1 20.5 35" style={{ stroke: "var(--acc)" }} strokeWidth="4.5" strokeLinecap="round"></path></svg>
          {CHAT_OPENING_TEXT}
        </p>
        {visible.map((m) => (
          <MessageRow key={m.id} message={m} meId={meId} memberById={memberById} />
        ))}
      </div>
      <p className="chat-notice" hidden={!errorText && !readonlyText}>{errorText ?? readonlyText}</p>
      <form
        className="lt-chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        <label htmlFor="msg" className="vh">メッセージ</label>
        <input
          id="msg"
          className="field"
          type="text"
          autoComplete="off"
          enterKeyHint="send"
          maxLength={1000}
          placeholder={left ? CHAT_LEFT_PLACEHOLDER_TEXT : CHAT_PLACEHOLDER_TEXT}
          disabled={inputDisabled}
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
        />
        <button type="submit" className="cta lt-send" aria-label="送信" disabled={sendDisabled}>
          <Icon name="send" size={20} />
        </button>
      </form>
    </section>
  );
};

const MessageRow = ({ message, meId, memberById }) => {
  const time = clockText(message.created_at);
  if (message.sender_id === null) {
    // システム行(参加取り消しの告知・05 §5 leave)。文言はサーバのbodyのまま
    return <p className="msg lt-system">{message.body}</p>;
  }
  if (message.sender_id === meId) {
    return (
      <div className="msg lt-msg mine">
        <span className="lt-msg-meta">あなた · {time}</span>
        <p className="lt-bubble">{message.body}</p>
      </div>
    );
  }
  const member = memberById.get(message.sender_id);
  return (
    <div className="msg lt-msg theirs">
      <span
        aria-hidden="true"
        className="lt-avatar lt-avatar--msg"
        style={{ background: member?.color ?? "var(--s3)" }}
      >
        {member?.initial ?? "?"}
      </span>
      <div className="lt-msg-body">
        <span className="lt-msg-meta">{member?.name ?? ""} · {time}</span>
        <p className="lt-bubble">{message.body}</p>
      </div>
    </div>
  );
};
