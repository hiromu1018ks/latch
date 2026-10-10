// 01「預ける(ホーム)」の配線(UI v2 Phase 2・プロトタイプ01が正源)。
// composerカード(フォーカス時の枠+glow・Zen Old Minchoの入力欄)・読み取りchips
// (aria-live=polite)・例文chips・CTA「読み解く」→#/confirm・下書き保存(D-13)。
// 01でparseまで済ませ、02(読み解き確認)はstateを共有して表示だけを組む。
// 意図文言をログに出さない(01 §21)。
import { applyParseFallback, applyParseResult, canDraft } from "./state.js";
import { createParseFlow } from "./parseFlow.js";
import { readSlots } from "./conditions.js";
import { createSaveFlow } from "./save.js";
import { DEFAULT_NOTIFICATION_LEVEL, DEFAULT_VISIBILITY } from "./format.js";
import { icon } from "../ui/icons.js";

// 例文chips(プロトタイプ01の固定4値)
export const EXAMPLES = [
  { tag: "飲み", text: "今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい" },
  { tag: "転職", text: "年収1,000万円以上なら転職を考える" },
  { tag: "売買", text: "この車が190万円以上なら売ってもいい" },
  { tag: "集まり", text: "4人集まるなら参加したい" },
];

const $ = (selector, root = document) => root.querySelector(selector);

export const initIntentScreen = ({ client, chrome, state }) => {
  const root = $("#homeScreen");
  const composer = $(".composer", root);
  const intentText = $("#intentText", root);
  const readStatus = $("#readStatus", root);
  const parseError = $("#parseError", root);
  const readChips = $("#readChips", root);
  const submitButton = $("#readButton", root);
  const draftButton = $("#draftButton", root);
  const ctaHelp = $("#ctaHelp", root);

  // --- 読み取りchips(aria-live=polite・未読み取りの2状態) ---------------------
  const refreshChips = () => {
    const nowIso = new Date().toISOString();
    const slots = readSlots(state, nowIso);
    const empty = state.rawText.trim().length === 0;
    const noread = !empty && slots.length === 0;

    readChips.replaceChildren(
      ...slots.map((slot) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        const label = document.createElement("span");
        label.className = "chip-label";
        label.textContent = slot.label;
        const value = document.createElement("span");
        value.className = "chip-value";
        value.textContent = slot.value;
        chip.append(label, value);
        return chip;
      }),
    );
    $("[data-guide]", root).hidden = !empty;
    $("[data-noread]", root).hidden = !noread;
    return slots.length;
  };

  // --- parse連携(debounce・design §2.4) --------------------------------------
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
      refreshChips();
      refreshActions();
    },
    onError: (err) => {
      state.parseStatus = "error";
      if (err?.code === "VALIDATION_ERROR") {
        // 構造化フォームへのフォールバック(確定値9): 02の行を手動入力で埋める
        parseError.hidden = true;
        Object.assign(state, applyParseFallback(state));
        refreshChips();
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
      refreshActions();
    },
  });

  // --- CTA「読み解く」(読み取り1件以上で有効) --------------------------------
  let busy = false;

  const refreshActions = () => {
    state.rawText = intentText.value; // 先に更新(chipsの空判定が1イベント遅れないように)
    const slotCount = refreshChips();
    const disabled = busy || slotCount === 0;
    submitButton.disabled = disabled;
    submitButton.setAttribute("aria-busy", String(busy));
    $(".cta-arrow", submitButton)?.remove();
    $(".spinner", submitButton)?.remove();
    submitButton.prepend(busy ? icon("spinner", 18) : icon("arrowRight", 18));
    $(".cta-label", submitButton).textContent = busy ? "読み解いています…" : "読み解く";
    ctaHelp.textContent = busy
      ? "時間・場所・人数・金額に分けています"
      : state.rawText.trim().length === 0
        ? "条件を書くと、読み解けます"
        : slotCount === 0
          ? "読み取れる条件が1つ以上必要です"
          : "次の画面で、読み取り結果を確認できます";
    draftButton.disabled = busy || !canDraft(state);
  };

  const goConfirm = async () => {
    if (busy || submitButton.disabled) return;
    busy = true;
    refreshActions();
    try {
      await parseFlow.commit(); // debounce中の入力を即時確定(古い結果での遷移を防ぐ)
    } catch {
      // parse失敗時はこの後のslotCount判定で遷移を見合わせる
    }
    busy = false;
    const slotCount = refreshChips();
    refreshActions(); // busy描画を確実に戻す(遷移後に01へ戻ってもCTAがbusyのまま残らない)
    if (slotCount > 0) location.hash = "#/confirm";
  };

  submitButton.addEventListener("click", () => {
    goConfirm();
  });
  intentText.addEventListener("input", () => {
    refreshActions();
    parseFlow.input(intentText.value);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      goConfirm();
    }
  });

  // --- 下書き保存(D-13: 01のcomposer直下の最小導線・APIは温存) ---------------
  const saveFlow = createSaveFlow({
    client,
    getState: () => state,
    getOptions: () => ({
      visibility: DEFAULT_VISIBILITY, // D-02: 選択UIなし・既定値で確定送信
      notificationLevel: DEFAULT_NOTIFICATION_LEVEL,
      expiresAt: null,
    }),
    onBusy: (b) => {
      draftButton.disabled = b || !canDraft(state);
    },
    onDraftSaved: () => {
      chrome.showToast("下書きを保存しました");
      refreshActions();
    },
    onActiveSaved: () => {},
    onFormError: () => {
      chrome.showToast("下書きを保存できませんでした");
    },
  });
  draftButton.addEventListener("click", () => saveFlow.saveDraft());

  // --- 例文chips(タップでtextareaへ) -----------------------------------------
  const examplesWrap = $("#exampleChips", root);
  examplesWrap.replaceChildren(
    ...EXAMPLES.map((example) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "example-chip ghost";
      const tag = document.createElement("span");
      tag.className = "example-tag";
      tag.textContent = example.tag;
      button.append(tag, example.text);
      button.addEventListener("click", () => {
        intentText.value = example.text;
        intentText.dispatchEvent(new Event("input", { bubbles: true }));
        intentText.focus();
      });
      return button;
    }),
  );

  // --- 初期化 ---------------------------------------------------------------
  refreshActions();
  return {
    focusComposer: () => {
      intentText.focus();
      intentText.select();
    },
    // 02で行編集した後に01へ戻ったときへ(chips・CTAを最新のstateで描き直す)
    refresh: refreshActions,
    screen: root,
    composer,
  };
};
