// エントリポイント(M3 ws-7 design §3・UI v2 Phase 2)。共通初期化(セッション・
// トークンパネル・シェル)+ルーター起動のみを担う。01の入力はscreen.js・
// 02の確認はconfirm.js(state共有)・ホーム・詳細は latch/* へ委譲。
// 意図文言をログに出さない(01 §21)。
import { initChrome } from "./ui/chrome.js";
import { createClient } from "./api/client.js";
import { createSession, exchangeIdpToken } from "./api/session.js";
import { initIntentScreen } from "./intent/screen.js";
import { initConfirmScreen } from "./intent/confirm.js";
import { createFormState } from "./intent/state.js";
import { createAppState } from "./appState.js";
import { createRouter } from "./router.js";
import { createHome } from "./latch/home.js";
import { createDetail } from "./latch/detail.js";
import { createReportFlow } from "./latch/report.js";
import { createNotice } from "./latch/notice.js";
import { createPermissionControl } from "./latch/permission.js";
import { createBlocksSection } from "./latch/blocks.js";
import { createSettings } from "./latch/settings.js";
import { createBlockFlow } from "./latch/blockFlow.js";
import { createConsent } from "./latch/consent.js";

const $ = (selector) => document.querySelector(selector);

const session = createSession();
// 422 CONSENT_REQUIREDの横断検出(誘導経路(b))。全Intent操作はこのcallを
// 通るため、個別画面の変更なしで全経路を拾う。consentは後段で生成するため
// 参照解決は呼び出し時(実行時)。
let consent;
const baseClient = createClient({
  session,
  onSessionExpired: () => showTokenPanel(),
});
const client = {
  call: async (method, path, opts) => {
    try {
      return await baseClient.call(method, path, opts);
    } catch (err) {
      if (err?.status === 422 && err?.code === "CONSENT_REQUIRED") {
        consent?.notifyRequired();
      }
      throw err;
    }
  },
};

const appState = createAppState({ client });

// --- シェル(navバッジ・aria-current・右上toast・dialog・popover) -------------
const chrome = initChrome({
  popovers: [
    { button: $("#noticeButton"), panel: $("#noticePopover") },
    { button: $("#accountButton"), panel: $("#accountPopover") },
  ],
  toast: $("#toast"),
  appDialog: $("#appDialog"),
});

// アカウントボタンは表示名のイニシャル1字(失敗時は空のまま)
if (session.hasTokens()) {
  appState
    .ensureMe()
    .then((me) => chrome.setAccountInitial(Array.from(me.display_name ?? "?")[0] ?? "?"))
    .catch(() => {});
}

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
    consent.ensureGuidance().catch(() => {}); // (a) 接続後の未同意誘導
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

// --- 01入力+02確認(state共有・UI v2 Phase 2) --------------------------------
const intentState = createFormState();
const intentScreen = initIntentScreen({ client, chrome, state: intentState });
const confirmScreen = initConfirmScreen({ client, chrome, state: intentState });

// 02の「書き直す」→01でcomposerへフォーカス(hash遷移後に当てる)
document.addEventListener("click", (event) => {
  if (event.target.closest('[data-action="rewrite"]')) {
    window.setTimeout(() => intentScreen.focusComposer(), 0);
  }
});

// --- ホーム・詳細・通報・お知らせ・設定(ws-7 §2.1〜§2.2・ws-8 §2.1〜§2.7) --
const home = createHome({
  client,
  el: {
    intentCards: $("#intentCards"),
    standbyLine: $("#standbyLine"),
    waitSection: $("#waitSection"),
  },
  onCounts: (partial) => chrome.setCounts(partial), // 待機中・候補・成立バッジ
});

// お知らせ(ws-8 design §2.1〜§2.2): 起動時1回のpreloadでドット判定
const notice = createNotice({
  client,
  el: {
    list: $("#noticeList"),
    moreButton: $("#noticeMoreButton"),
    dot: $("#notificationDot"),
  },
});
if (session.hasTokens()) notice.preload().catch(() => {});
$("#noticeMoreButton").addEventListener("click", () => notice.loadMore().catch(() => {}));
// popoverを開いたことの検知は追加リスナ(chromeのリスナが先に登録済みのため
// このリスナの実行時にはhiddenは開閉後 — 開いたときだけ取得・既読化)
$("#noticeButton").addEventListener("click", () => {
  if (!$("#noticePopover").hidden) notice.open().catch(() => {});
});

const reportFlow = createReportFlow({ client, chrome, modal: $("#reportModal") });
const blockFlow = createBlockFlow({
  client,
  chrome,
  modal: $("#blockModal"),
  onBlocked: () => detail.refresh(), // ブロック成功で詳細再取得(D-23収束)
});
const detail = createDetail({
  client,
  appState,
  chrome,
  root: $("#detailScreen"),
  reportFlow,
  blockFlow,
});

// 設定画面(ws-8 design §2.4〜§2.6): 通知許可(DI)+ブロック管理
const permissionControl = createPermissionControl({
  notificationApi: typeof Notification !== "undefined" ? Notification : undefined,
  mount: $("#permissionControl"),
});
const blocks = createBlocksSection({
  client,
  el: { list: $("#blockList"), moreButton: $("#blockMoreButton") },
  modal: $("#unblockModal"),
});
$("#blockMoreButton").addEventListener("click", () => blocks.loadMore().catch(() => {}));
consent = createConsent({
  client,
  chrome,
  el: { onboarding: $("#consentOnboarding"), settings: $("#consentControl") },
});
const settings = createSettings({ permissionControl, blocks, consent });
$("#settingsButton").addEventListener("click", () => {
  // popoverを閉じて設定画面へ(chromeのclosePopoversと同じ操作をmain.js側で実行)
  location.hash = "#/settings";
  $("#accountPopover").hidden = true;
  $("#accountButton").setAttribute("aria-expanded", "false");
});

// ルート→topnav/tabbarの強調先(詳細=候補扱いは暫定・Phase 3/4で提案/成立を分岐)
const NAV_BY_ROUTE = {
  home: "deposit",
  confirm: "deposit", // 02は「預ける」流れの中(Phase 2)
  standby: "standby",
  latch: "proposals",
};

const router = createRouter({
  screens: {
    home: $("#homeScreen"),
    confirm: $("#confirmScreen"), // UI v2 02(Phase 2で実装)
    standby: $("#standbyScreen"), // UI v2 03(Phase 3で実装)
    latch: $("#detailScreen"),
    settings: $("#settingsScreen"),
    onboarding: $("#onboardingScreen"),
  },
  onRoute: (route) => {
    chrome.setCurrentNav(NAV_BY_ROUTE[route.name] ?? null);
    if (route.name === "home") {
      home.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
      home.loadIntents().catch(() => {});
    } else if (route.name === "confirm") {
      confirmScreen.enter();
    } else if (route.name === "settings") {
      settings.show();
    } else if (route.name === "onboarding") {
      consent.show().catch(() => {});
    } else if (route.name === "latch") {
      detail.show(route.id).catch(() => {});
    }
    // standbyはPhase 3までプレースホルダ表示のみ
  },
});
router.start();
// (a) トークン接続済みで起動した場合も未同意なら誘導
if (session.hasTokens()) consent.ensureGuidance().catch(() => {});
