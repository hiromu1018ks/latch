// エントリポイント(03 §3の入力フロー・design §2.2〜§2.9)。
// DOM配線のみを担い、ロジックは各モジュールへ委ねる。意図文言をログに出さない(01 §21)。
import { initChrome } from "./ui/chrome.js";
import { createClient } from "./api/client.js";
import { createSession, exchangeIdpToken } from "./api/session.js";
import { PRIVACY_VALUES, NOTIFICATION_VALUES } from "./intent/format.js";
import {
  applyParseFallback,
  applyParseResult,
  canDraft,
  canSubmit,
  createFormState,
  missingRequired,
} from "./intent/state.js";
import { createParseFlow } from "./intent/parseFlow.js";
import {
  NG_NOTE_TEXT,
  REQUIRED_NOTE_TEXT,
  attachAddCondition,
  beginRowEdit,
  renderConditions,
} from "./intent/conditions.js";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "./intent/expiry.js";
import { createSaveFlow } from "./intent/save.js";

const $ = (selector) => document.querySelector(selector);

const session = createSession();
const client = createClient({
  session,
  onSessionExpired: () => showTokenPanel(),
});

const state = createFormState();

// --- 画面装飾(テーマ・popover・トースト・モーダル) -------------------------
const chrome = initChrome({
  themeButton: $("#themeButton"),
  popovers: [
    { button: $("#noticeButton"), panel: $("#noticePopover") },
    { button: $("#accountButton"), panel: $("#accountPopover") },
  ],
  toast: $("#toast"),
  modal: {
    root: $("#confirmationModal"),
    closeButton: $("#modalClose"),
    returnButton: $("#returnButton"),
  },
});

// --- 開発用トークンパネル(design §2.3・トークン未設定時のみ表示) ----------
const tokenPanel = $("#tokenPanel");
const tokenError = $("#tokenError");

function showTokenPanel() {
  tokenPanel.hidden = false;
  tokenError.hidden = true;
}

if (!session.hasTokens()) showTokenPanel();

$("#tokenConnect").addEventListener("click", async () => {
  const idpToken = $("#idpToken").value.trim();
  if (!idpToken) return;
  const button = $("#tokenConnect");
  button.disabled = true;
  try {
    const tokens = await exchangeIdpToken((...args) => fetch(...args), {
      provider: "google",
      idpToken,
    });
    session.save(tokens);
    tokenPanel.hidden = true;
    $("#idpToken").value = "";
  } catch (err) {
    tokenError.textContent =
      err?.status === 401
        ? "トークンが無効です。再発行して貼り直してください。"
        : "接続できませんでした。APIの起動を確認してください。";
    tokenError.hidden = false;
  } finally {
    button.disabled = false;
  }
});

// --- 条件リスト ------------------------------------------------------------
const conditionList = $("#conditionList");
const requiredNote = $("#requiredNote");
const parseError = $("#parseError");
const globalError = $("#globalError");

const rerender = () => {
  renderConditions({ listEl: conditionList, state, now: new Date().toISOString() });
  const missing = missingRequired(state);
  requiredNote.hidden = missing.length === 0;
};

conditionList.addEventListener("click", (event) => {
  const editButton = event.target.closest(".edit-button");
  if (editButton) {
    beginRowEdit({ rowEl: editButton.closest(".condition-row"), state, rerender });
    refreshActions();
  }
});

const addConditionForm = $("#addConditionForm");
$("#addConditionButton").addEventListener("click", () => {
  addConditionForm.hidden = false;
  $("#addConditionButton").hidden = true;
  $("#newConditionValue").focus();
});
$("#cancelAdd").addEventListener("click", () => {
  addConditionForm.hidden = true;
  $("#addConditionButton").hidden = false;
  $("#newConditionValue").value = "";
});
attachAddCondition({
  formEl: addConditionForm,
  state,
  rerender: () => {
    rerender();
    $("#addConditionButton").hidden = false; // 追加フォームはattachAddCondition内で閉じる
  },
});

// --- parse連携(debounce・design §2.4) ------------------------------------
const showParseError = (message, retryable) => {
  parseError.replaceChildren(document.createTextNode(message));
  if (retryable) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "parse-retry";
    button.textContent = "もう一度読み取る"; // 07 D-17: 入力テキストを保持した再試行
    button.addEventListener("click", () => parseFlow.retry());
    parseError.append(button);
  }
  parseError.hidden = false;
};

const parseFlow = createParseFlow({
  send: (text, { signal }) =>
    client.call("POST", "/v1/intents/parse", { body: { text }, signal }),
  onResult: (data) => {
    parseError.hidden = true;
    Object.assign(state, applyParseResult(state, data.structured_intent));
    rerender();
    refreshExpiry(state.structured.time.start);
    refreshActions();
  },
  onError: (err) => {
    state.parseStatus = "error";
    if (err?.code === "VALIDATION_ERROR") {
      // 構造化フォームへのフォールバック(確定値9): 全行が手動入力可能な空状態
      parseError.hidden = true;
      Object.assign(state, applyParseFallback(state));
      rerender();
      refreshActions();
      return;
    }
    if (err?.code === "RATE_LIMITED") {
      showParseError("操作が集中しています。少し時間をおいてから入力し直してください。", false);
    } else if (err?.code === "LLM_UNAVAILABLE") {
      showParseError("ただいま条件を読み取れません。", true);
    } else if (err?.code === "UNAUTHENTICATED") {
      return; // パネル表示済み(onSessionExpired)
    } else {
      showParseError("通信エラーが発生しました。", true);
    }
  },
});

// --- 入力テキスト(300字上限はmaxlength・カウンタ常時表示) ----------------
const intentText = $("#intentText");
const characterCount = $("#characterCount");
const submitButton = $("#submitButton");
const draftButton = $("#draftButton");

const refreshActions = () => {
  characterCount.textContent = `${intentText.value.length} / 300`;
  state.rawText = intentText.value;
  submitButton.disabled = !canSubmit(state);
  draftButton.disabled = !canDraft(state);
};

intentText.addEventListener("input", () => {
  refreshActions();
  parseFlow.input(intentText.value);
});

// --- 有効期限(design §2.6) ----------------------------------------------
const expirySelect = $("#expiry");

async function refreshExpiry(timeStartIso) {
  try {
    const data = await fetchExpiryOptions(client, timeStartIso ?? null);
    applyExpiryOptions(expirySelect, data);
    expirySelect.disabled = false;
    expirySelect.closest(".setting-group").querySelector("label").textContent =
      "有効期限";
  } catch {
    // 取得失敗時は既存の選択肢のまま続行(保存時は現在の選択値を送る)
  }
}

// --- 保存(active/draft・design §2.9) ------------------------------------
const saveFlow = createSaveFlow({
  client,
  getState: () => state,
  getOptions: () => ({
    visibility:
      PRIVACY_VALUES[$("#privacy").value] ?? "hidden_until_match",
    notificationLevel:
      NOTIFICATION_VALUES[$("#notification").value] ?? "proposals_only",
    expiresAt: selectedExpiry(expirySelect).expiresAt,
  }),
  onBusy: (busy) => {
    submitButton.disabled = busy || !canSubmit(state);
    draftButton.disabled = busy || !canDraft(state);
  },
  onActiveSaved: () => {
    chrome.closeModal({ submitButton }); // モーダルを閉じ同一画面に留まる(03 §3)
    refreshActions();
  },
  onDraftSaved: () => {
    chrome.showToast("下書きを保存しました"); // プロトタイプ文言
    refreshActions();
  },
  onFormError: (placement) => {
    if (placement.target === "location" || placement.target === "time") {
      // 条件リストへ戻して修正を促す(確定値11・design §2.9) — モーダルを閉じる
      chrome.closeModal({});
      const row = conditionList.querySelector(
        `[data-label="${placement.target === "location" ? "場所" : "時間"}"]`,
      );
      row?.classList.add("condition-error");
      row?.setAttribute("title", placement.note);
      globalError.hidden = true;
    } else {
      globalError.textContent = placement.note;
      globalError.hidden = false;
    }
  },
});

const openConfirmation = async () => {
  if (!canSubmit(state)) return;
  // モーダルを開く時に期限を選択肢の最新状態へ再取得(時間経過で過ぎた選択肢の
  // disabled化・既定の再計算 — design §2.6・確定値6)
  await refreshExpiry(state.structured.time.start);
  const { label } = selectedExpiry(expirySelect);
  chrome.openModal({
    expiryLabel: label ?? "—",
    privacyLabel: $("#privacy").value,
  });
};

submitButton.addEventListener("click", openConfirmation);
$("#returnButton").addEventListener("click", () => saveFlow.saveActive());
draftButton.addEventListener("click", () => saveFlow.saveDraft());

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    openConfirmation();
  }
  if (event.key === "Escape") {
    if (!$("#confirmationModal").hidden) chrome.closeModal({});
  }
});

// --- 初期化 ---------------------------------------------------------------
rerender();
refreshActions();
