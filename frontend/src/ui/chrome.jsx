// アプリシェルの制御(UI v2 Phase 6・React版)。旧src/ui/chrome.js(initChrome)を
// コンテキスト+hooksへ載せ替えたもの。担うのは ①topnav/tabbarのaria-currentと
// 件数バッジ ②右上toast ③<dialog class="dlg">の土台 ④アカウントイニシャル。
// APIの形(showToast/setCounts/setCurrentNav/setAccountInitial/openAppDialog/
// closeAppDialog)は旧実装と同じため、呼び出し側の画面ロジックは変わらない。
// openAppDialogのbodyはReactNodeに加え、移行期の互換として旧画面モジュールが
// 渡す生のNodeも受理する(instanceof Nodeで判別してreplaceChildrenする)。
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Icon, Logo } from "./Icon.jsx";

const TOAST_AUTO_HIDE_MS = 2400;

const ChromeContext = createContext(null);

export const useChrome = () => useContext(ChromeContext);

export const ChromeProvider = ({ children }) => {
  const [toast, setToast] = useState(null); // {kind:"plain"|"candidate", ...}
  const [counts, setCountsState] = useState({ standby: 0, proposals: 0, matched: 0 });
  const [currentNav, setCurrentNavState] = useState(null);
  const [accountInitial, setAccountInitialState] = useState("");
  const [dialog, setDialog] = useState(null); // {title, body, actions}
  const toastTimer = useRef(null);

  const showToast = useCallback((messageOrOptions) => {
    window.clearTimeout(toastTimer.current);
    if (typeof messageOrOptions === "object" && messageOrOptions !== null) {
      const { message, href, kicker = "LATCH · いま" } = messageOrOptions;
      setToast({ kind: "candidate", message, href, kicker }); // 閉じるまで残る(プロトタイプ03)
      return;
    }
    setToast({ kind: "plain", message: messageOrOptions });
    toastTimer.current = window.setTimeout(() => setToast(null), TOAST_AUTO_HIDE_MS);
  }, []);

  const hideToast = useCallback(() => {
    window.clearTimeout(toastTimer.current);
    setToast(null);
  }, []);

  const setCounts = useCallback((partial) => {
    setCountsState((prev) => ({ ...prev, ...partial }));
  }, []);

  const setCurrentNav = useCallback((name) => setCurrentNavState(name), []);

  const setAccountInitial = useCallback((letter) => setAccountInitialState(letter), []);

  const openAppDialog = useCallback(({ title, body = null, actions = [] }) => {
    setDialog({ title, body, actions });
  }, []);

  const dismissDialog = useCallback(() => setDialog(null), []);

  const value = useMemo(
    () => ({
      showToast,
      hideToast,
      setCounts,
      setCurrentNav,
      setAccountInitial,
      openAppDialog,
      dismissDialog,
      toast,
      counts,
      currentNav,
      accountInitial,
      dialog,
    }),
    [showToast, hideToast, setCounts, setCurrentNav, setAccountInitial, openAppDialog, dismissDialog, toast, counts, currentNav, accountInitial, dialog],
  );

  return <ChromeContext.Provider value={value}>{children}</ChromeContext.Provider>;
};

// --- 右上toast(03のtoast-pos/toast) ---------------------------------------
// 2形: 文言toast=従来型(自動消滅)・候補トースト(03の構成・ロゴ+「見る」リンク・
// 自動消滅しない)。旧chrome.jsの生成するDOMと同形。
export const ToastArea = () => {
  const { toast, hideToast } = useChrome();
  const close = (
    <button type="button" className="toast-close ghost" aria-label="通知を閉じる" onClick={hideToast}>
      <Icon name="close" size={18} />
    </button>
  );
  return (
    <div className="toast-pos" id="toast" role="status" aria-live="polite">
      {toast == null ? null : (
        <div className="toast">
          {toast.kind === "candidate" ? (
            <>
              <a className="toast-link" href={toast.href}>
                <span className="toast-logo">
                  <Logo size={24} />
                </span>
                <span className="toast-body">
                  <span className="toast-kicker">{toast.kicker}</span>
                  <span className="toast-msg">{toast.message}</span>
                </span>
                <span className="toast-see">
                  見る
                  <Icon name="arrowRight" size={15} />
                </span>
              </a>
              {close}
            </>
          ) : (
            <>
              <p className="toast-msg toast-msg--plain">{toast.message}</p>
              {close}
            </>
          )}
        </div>
      )}
    </div>
  );
};

const ACTION_STYLES = {
  ghost: { border: "1px solid var(--line-strong)", background: "transparent", color: "var(--t1)" },
  solid: { border: "none", background: "var(--t1)", color: "var(--bg)" },
  danger: { border: "none", background: "var(--dng)", color: "#1A0A08", fontWeight: 700 },
};

// appDialog(03の取り下げ・05の成立取り消しなどが使う汎用<dialog>)。
// 旧chrome.openAppDialogと同形の題+body+操作列を組む。bodyはReactNode。
// 移行期の互換: 旧画面モジュールは生のNodeを渡すため、その場合は
// replaceChildrenで差し込む(Reactの管理外として扱う)。
export const AppDialog = () => {
  const { dialog, dismissDialog } = useChrome();
  const ref = useRef(null);
  const bodyHost = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (dialog && !el.open) el.showModal();
  }, [dialog]);

  // dialogが閉じたら(Esc・close()含む)状態を空にする
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.addEventListener("close", dismissDialog);
    return () => el.removeEventListener("close", dismissDialog);
  }, [dismissDialog]);

  // bodyが生のNode(移行期)なら子として差し込む。ReactNodeならJSXで描く
  useEffect(() => {
    const host = bodyHost.current;
    if (!host) return;
    if (dialog?.body instanceof Node) {
      host.replaceChildren(dialog.body);
    } else {
      host.replaceChildren();
    }
  }, [dialog]);

  // backdropクリックで閉じる(Escは<dialog>標準)
  const onBackdropClick = (event) => {
    if (event.target === ref.current && ref.current.open) ref.current.close();
  };

  return (
    <dialog className="dlg" id="appDialog" ref={ref} onClick={onBackdropClick}>
      {dialog == null ? null : (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {dialog.title ? (
            <h2
              style={{
                margin: 0,
                fontFamily: "'Zen Old Mincho','Hiragino Mincho ProN','Yu Mincho',serif",
                fontSize: "22px",
                fontWeight: 700,
              }}
            >
              {dialog.title}
            </h2>
          ) : null}
          {dialog.body instanceof Node ? <div ref={bodyHost} /> : dialog.body}
          {dialog.actions?.length > 0 ? (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "flex-end",
                gap: "8px",
                marginTop: "4px",
              }}
            >
              {dialog.actions.map((action) => {
                const variant = action.variant ?? "ghost";
                return (
                  <button
                    key={action.label}
                    type="button"
                    className={variant === "ghost" ? "ghost" : ""}
                    style={{
                      minHeight: "48px",
                      padding: "0 20px",
                      borderRadius: "999px",
                      fontFamily: "inherit",
                      fontSize: "15px",
                      cursor: "pointer",
                      ...ACTION_STYLES[variant],
                    }}
                    onClick={() => {
                      ref.current?.close();
                      action.onClick?.();
                    }}
                  >
                    {action.label}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      )}
    </dialog>
  );
};
