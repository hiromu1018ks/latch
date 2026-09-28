// 画面装飾の初期化(prototype app.jsから分離・design §3.1)。
// 変更点: ①トーストは文言を引数で受ける ②確認モーダルのsummaryは
// 選択値(label)を引数で受ける(expiryのvalueが絶対時刻になったため)。

const root = document.documentElement;

const updateThemeButton = (themeButton) => {
  const dark = root.dataset.theme === "dark";
  themeButton.setAttribute("aria-pressed", String(dark));
  themeButton.setAttribute(
    "aria-label",
    dark ? "ライトモードに切り替える" : "ダークモードに切り替える",
  );
  themeButton.querySelector("i").className = dark ? "ph ph-sun" : "ph ph-moon";
};

export const initChrome = ({ themeButton, popovers, toast, modal }) => {
  const setTheme = (theme, persist = false) => {
    root.dataset.theme = theme;
    if (persist) localStorage.setItem("latch-theme", theme);
    updateThemeButton(themeButton);
  };

  themeButton.addEventListener("click", () => {
    setTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
  });
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (event) => {
      if (!localStorage.getItem("latch-theme"))
        setTheme(event.matches ? "dark" : "light");
    });
  updateThemeButton(themeButton);

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

  let toastTimer;
  const showToast = (message) => {
    window.clearTimeout(toastTimer);
    const icon = toast.querySelector("i"); // プロトタイプのcheckアイコンを維持
    toast.replaceChildren(icon, ` ${message}`);
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2400);
  };

  const openModal = ({ expiryLabel, privacyLabel }) => {
    const summary = modal.root.querySelector(".confirmation-summary");
    summary.replaceChildren();
    const clock = document.createElement("i");
    clock.className = "ph ph-clock";
    clock.setAttribute("aria-hidden", "true");
    summary.append(clock, ` ${expiryLabel}まで`);
    const lock = document.createElement("i");
    lock.className = "ph ph-lock";
    lock.setAttribute("aria-hidden", "true");
    summary.append(lock, ` ${privacyLabel === "条件一致までは非公開" ? "成立までは非公開" : privacyLabel}`);
    modal.root.hidden = false;
    document.body.classList.add("modal-open");
    modal.closeButton.focus();
  };
  const closeModal = ({ submitButton }) => {
    modal.root.hidden = true;
    document.body.classList.remove("modal-open");
    submitButton?.focus();
  };

  // 「Intentを確認する」(returnButton)のclickでモーダルを閉じない —
  // 保存成功(onActiveSaved)で閉じる(design §2.9)。保存失敗時はモーダルを
  // 開いたまま main.js のonFormErrorが扱う
  modal.closeButton.addEventListener("click", () => closeModal({}));
  modal.root.addEventListener("click", (event) => {
    if (event.target === modal.root) closeModal({});
  });

  return { showToast, openModal, closeModal };
};
