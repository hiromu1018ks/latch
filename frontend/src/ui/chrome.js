// アプリシェルの制御(UI v2 Phase 2)。テーマ切替・「Intent N件」popover・旧確認
// モーダルは廃止(gap分析§2・確認は02画面が担う)。担うのは ①topnav/tabbarの
// aria-currentと件数バッジ ②右上toast ③<dialog class="dlg">の土台
// ④popover(お知らせ・アカウント)。デザイン値はprototype/v2の共通<style>と
// 03(drop-dlg・toast)に拠る。
import { icon } from "./icons.js";

const TOAST_AUTO_HIDE_MS = 2400;

export const initChrome = ({ popovers = [], toast, appDialog }) => {
  // --- nav(tabbar・topnav共通のdata-nav属性) --------------------------------
  const navLinks = () => [
    ...document.querySelectorAll(".topnav [data-nav], .tabbar [data-nav]"),
  ];

  const setCurrentNav = (name) => {
    for (const link of navLinks()) {
      if (link.dataset.nav === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  };

  // --- 件数バッジ(待機中=stb・候補/成立=acc・0件は非表示) ---------------------
  const counts = { standby: 0, proposals: 0, matched: 0 };

  const setCounts = (partial) => {
    Object.assign(counts, partial);
    for (const [key, n] of Object.entries(counts)) {
      for (const pill of document.querySelectorAll(`[data-count="${key}"]`)) {
        pill.hidden = !(n > 0);
        if (n > 0) {
          const num = document.createElement("span");
          num.textContent = String(n);
          const unit = document.createElement("span");
          unit.className = "vh";
          unit.textContent = "件";
          pill.replaceChildren(num, unit);
        }
      }
    }
  };

  // --- アカウントボタン(イニシャル1字) --------------------------------------
  const setAccountInitial = (letter) => {
    const button = document.querySelector(".account-button");
    if (button) button.textContent = letter;
  };

  // --- 右上toast(03のtoast-pos/toast・自動消滅は現行挙動を維持) -------------
  let toastTimer;

  const hideToast = () => {
    window.clearTimeout(toastTimer);
    toast.replaceChildren();
  };

  const showToast = (message) => {
    window.clearTimeout(toastTimer);
    const card = document.createElement("div");
    card.className = "toast";
    const msg = document.createElement("p");
    msg.className = "toast-msg";
    msg.textContent = message; // 文言はそのまま設定する(HTMLとして解釈しない)
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close ghost";
    close.setAttribute("aria-label", "通知を閉じる");
    close.append(icon("close", 18));
    close.addEventListener("click", hideToast);
    card.append(msg, close);
    toast.replaceChildren(card);
    toastTimer = window.setTimeout(hideToast, TOAST_AUTO_HIDE_MS);
  };

  // --- <dialog class="dlg">の土台(03 drop-dlgの構成) ------------------------
  const ACTION_STYLES = {
    ghost: "border:1px solid var(--line-strong);background:transparent;color:var(--t1)",
    solid: "border:none;background:var(--t1);color:var(--bg)",
    danger: "border:none;background:var(--dng);color:#1A0A08;font-weight:700",
  };

  const closeAppDialog = () => {
    if (appDialog.open) appDialog.close();
  };

  const openAppDialog = ({ title, body = null, actions = [] }) => {
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:16px";
    if (title) {
      const heading = document.createElement("h2");
      heading.style.cssText =
        "margin:0;font-family:'Zen Old Mincho','Hiragino Mincho ProN','Yu Mincho',serif;font-size:22px;font-weight:700";
      heading.textContent = title;
      wrap.append(heading);
    }
    if (body) wrap.append(body);
    if (actions.length > 0) {
      const row = document.createElement("div");
      row.style.cssText =
        "display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px;margin-top:4px";
      for (const action of actions) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = action.variant === "ghost" ? "ghost" : "";
        button.style.cssText = `min-height:48px;padding:0 20px;border-radius:999px;font-family:inherit;font-size:15px;cursor:pointer;${ACTION_STYLES[action.variant ?? "ghost"]}`;
        button.textContent = action.label;
        button.addEventListener("click", () => {
          closeAppDialog();
          action.onClick?.();
        });
        row.append(button);
      }
      wrap.append(row);
    }
    appDialog.replaceChildren(wrap);
    if (!appDialog.open) appDialog.showModal();
  };

  // backdropクリックで閉じる(Escは<dialog>標準)
  appDialog?.addEventListener("click", (event) => {
    if (event.target === appDialog) closeAppDialog();
  });

  // --- popover(お知らせ・アカウント) -----------------------------------------
  const closePopovers = (except = null) => {
    for (const { button, panel } of popovers) {
      if (panel !== except) {
        panel.hidden = true;
        button.setAttribute("aria-expanded", "false");
      }
    }
  };
  for (const { button, panel } of popovers) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = panel.hidden;
      closePopovers(panel);
      panel.hidden = !willOpen;
      button.setAttribute("aria-expanded", String(willOpen));
    });
    panel.addEventListener("click", (event) => event.stopPropagation());
  }
  document.addEventListener("click", () => closePopovers());

  return {
    showToast,
    setCounts,
    setCurrentNav,
    setAccountInitial,
    openAppDialog,
    closeAppDialog,
  };
};
