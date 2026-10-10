// 02「読み解き確認」の配線(UI v2 Phase 2・プロトタイプ02が正源)。
// 3ステップインジケータ(index.html静的)・あなたの言葉(D-03の値マッチング
// ハイライト)・読み取った条件5行+行編集・人数確認seg(D-04)・足せる条件
// トグル・公開範囲と通知カード+期限select(D-01・D-02)・CTA「この条件で
// 預ける」(人数確認必須)→保存→#/standby。stateは01と共有。
// 意図文言をログに出さない(01 §21)。
import { missingRequired } from "./state.js";
import { buildWordsSegments } from "./highlight.js";
import { beginRowEdit, renderExtras, renderRows, rowModels } from "./conditions.js";
import { createSaveFlow } from "./save.js";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "./expiry.js";
import { DEFAULT_NOTIFICATION_LEVEL, DEFAULT_VISIBILITY } from "./format.js";
import { icon } from "../ui/icons.js";

const $ = (selector, root = document) => root.querySelector(selector);

export const initConfirmScreen = ({ client, chrome, state }) => {
  const root = $("#confirmScreen");
  const wordsText = $("#wordsText", root);
  const rowsEl = $("#confirmRows", root);
  const extrasEl = $("#extraChips", root);
  const expirySelect = $("#expiry", root);
  const expiryLabel = $("#expiryLabel", root);
  const summaryEl = $("#slotSummary", root);
  const summaryDot = $("#slotSummaryDot", root);
  const cta = $("#depositButton", root);
  const ctaLabel = $(".cta-label", cta);
  const ctaHelp = $("#depositHelp", root);
  const errorEl = $("#confirmError", root);

  let extrasPicked = [];
  let busy = false;

  // --- あなたの言葉(原文ハイライト・D-03) ------------------------------------
  const renderWords = () => {
    const nowIso = new Date().toISOString();
    wordsText.replaceChildren("「");
    for (const segment of buildWordsSegments(state.structured, state.rawText, nowIso)) {
      if (segment.no != null) {
        const mark = document.createElement("mark");
        mark.className = "w";
        mark.append(segment.text);
        const sup = document.createElement("sup");
        const vhOpen = document.createElement("span");
        vhOpen.className = "vh";
        vhOpen.textContent = "（条件";
        const no = document.createTextNode(segment.no.replace(/^0/, ""));
        const vhClose = document.createElement("span");
        vhClose.className = "vh";
        vhClose.textContent = "）";
        sup.append(vhOpen, no, vhClose);
        mark.append(sup);
        wordsText.append(mark);
      } else {
        wordsText.append(segment.text);
      }
    }
    wordsText.append("」");
  };

  // --- 読み取った条件(5行)・足せる条件・NG注記 -------------------------------
  const rerenderRows = () => {
    renderRows({ listEl: rowsEl, state, nowIso: new Date().toISOString() });
    renderExtras({ wrapEl: extrasEl, picked: extrasPicked, onToggle: toggleExtra });
  };

  const toggleExtra = (label) => {
    extrasPicked = extrasPicked.includes(label)
      ? extrasPicked.filter((l) => l !== label)
      : [...extrasPicked, label];
    rerenderRows();
    refreshActions();
  };

  rowsEl.addEventListener("click", (event) => {
    const editButton = event.target.closest('[data-action="edit-row"]');
    if (editButton) {
      beginRowEdit({
        rowEl: editButton.closest(".slot-row"),
        state,
        nowIso: new Date().toISOString(),
        rerender: () => {
          rerenderRows();
          refreshActions();
        },
      });
    }
  });
  rowsEl.addEventListener("change", (event) => {
    if (event.target.dataset.action === "pick-includes-owner") {
      state.participantsIncludesOwner = event.target.value === "in";
      rerenderRows();
      refreshActions();
      rowsEl.querySelector('input[name="includes-owner"]:checked')?.focus();
    }
  });

  // --- 公開範囲と通知(D-02: 説明3項目+期限selectのみ・選択UIなし) -------------
  const refreshExpiry = async () => {
    try {
      const data = await fetchExpiryOptions(client, state.structured.time.start);
      applyExpiryOptions(expirySelect, data);
      expirySelect.disabled = false;
    } catch {
      // 取得失敗時は既存の選択肢のまま続行(保存時は現在の選択値を送る)
    }
    syncExpiryLabel();
  };

  const syncExpiryLabel = () => {
    const { label } = selectedExpiry(expirySelect);
    expiryLabel.textContent = label ?? "—";
  };
  expirySelect.addEventListener("change", syncExpiryLabel);

  // --- CTA「この条件で預ける」(人数確認必須・D-04) ---------------------------
  const refreshActions = () => {
    const answered = state.participantsIncludesOwner != null;
    const missing = missingRequired(state);
    const unconfirmed = (answered ? 0 : 1) + missing.length;
    summaryEl.textContent =
      unconfirmed === 0
        ? "5項目 · すべて確認済み"
        : `5項目 · 確認が必要なもの ${unconfirmed}件`;
    summaryDot.classList.toggle("ok", unconfirmed === 0);

    cta.disabled = busy || !answered || missing.length > 0;
    cta.setAttribute("aria-busy", String(busy));
    $(".spinner", cta)?.remove();
    $(".latch-mark", cta)?.remove();
    cta.prepend(busy ? icon("spinner", 20) : (() => {
      const mark = icon("latchMark", 22);
      mark.classList.add("latch-mark");
      return mark;
    })());
    ctaLabel.textContent = busy ? "預けています…" : "この条件で預ける";
    ctaHelp.className = unconfirmed === 0 ? "cta-help" : "cta-help strong";
    ctaHelp.textContent = busy
      ? "サーバーと照らし合わせています"
      : !answered
        ? "「03 人数」の確認に答えると、預けられます。"
        : missing.length > 0
          ? "時間・場所・目的を指定すると預けられます"
          : "預けたあとも、成立するまではいつでも直したり取り下げたりできます。";
  };

  // --- 保存(02で確定保存→#/standby。D-13: 下書き導線は01側) ------------------
  const saveFlow = createSaveFlow({
    client,
    getState: () => state,
    getOptions: () => ({
      visibility: DEFAULT_VISIBILITY, // D-02: 既定値で確定送信
      notificationLevel: DEFAULT_NOTIFICATION_LEVEL,
      expiresAt: selectedExpiry(expirySelect).expiresAt,
      softExtras: extrasPicked,
    }),
    onBusy: (b) => {
      busy = b;
      refreshActions();
    },
    onActiveSaved: () => {
      location.hash = "#/standby"; // 03(Phase 3実装。着地状態は03側で表現)
    },
    onDraftSaved: () => {},
    onFormError: (placement) => {
      errorEl.hidden = false;
      if (placement.target === "location" || placement.target === "time") {
        errorEl.textContent = placement.note;
        const key = placement.target === "location" ? "location" : "time";
        rowsEl.querySelector(`.slot-row[data-key="${key}"]`)?.classList.add("slot-error");
      } else {
        errorEl.textContent = placement.note;
      }
    },
  });
  cta.addEventListener("click", () => saveFlow.saveActive());

  // --- 画面へ入る(route時・01を経由せず来たら01へ戻す) ------------------------
  const enter = () => {
    if (!state.rawText.trim()) {
      location.hash = "#/";
      return;
    }
    errorEl.hidden = true;
    renderWords();
    rerenderRows();
    refreshActions();
    refreshExpiry();
  };

  return { enter };
};
