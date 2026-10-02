// エントリポイント(M3 ws-7 design §3)。共通初期化(テーマ・セッション・
// トークンパネル)+ルーター起動のみを担う。入力画面の配線は screen.js、
// ホーム・詳細は latch/* へ委譲。意図文言をログに出さない(01 §21)。
import { initChrome } from "./ui/chrome.js";
import { createClient } from "./api/client.js";
import { createSession, exchangeIdpToken } from "./api/session.js";
import { initIntentScreen } from "./intent/screen.js";
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

const $ = (selector) => document.querySelector(selector);

const session = createSession();
const client = createClient({
  session,
  onSessionExpired: () => showTokenPanel(),
});

// --- 画面装飾(テーマ・popover・トースト・モーダル) -------------------------
// Active Intent一覧をpopoversへ追加(ws-7 §2.2・関数は無変更)
const chrome = initChrome({
  themeButton: $("#themeButton"),
  popovers: [
    { button: $("#noticeButton"), panel: $("#noticePopover") },
    { button: $("#accountButton"), panel: $("#accountPopover") },
    { button: $("#intentCountButton"), panel: $("#intentPopover") },
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

// --- 入力画面(既存ロジック・screen.jsへ切り出し) ---------------------------
initIntentScreen({ client, chrome });

// --- ホーム・詳細・通報・お知らせ・設定(ws-7 §2.1〜§2.2・ws-8 §2.1〜§2.7) --
const appState = createAppState({ client });
const home = createHome({
  client,
  el: {
    candidateList: $("#candidateList"),
    matchedList: $("#matchedList"),
    moreButton: $("#moreButton"),
    intentCount: $("#intentCountButton"),
    intentList: $("#intentList"),
  },
});
$("#moreButton").addEventListener("click", () => home.loadMore().catch(() => {}));

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
const settings = createSettings({ permissionControl, blocks });
$("#settingsButton").addEventListener("click", () => {
  // popoverを閉じて設定画面へ(chromeのclosePopoversと同じ操作をmain.js側で実行)
  location.hash = "#/settings";
  $("#accountPopover").hidden = true;
  $("#accountButton").setAttribute("aria-expanded", "false");
});

const router = createRouter({
  screens: {
    home: $("#homeScreen"),
    latch: $("#detailScreen"),
    settings: $("#settingsScreen"),
  },
  onRoute: (route) => {
    if (route.name === "home") {
      home.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
      home.loadIntents().catch(() => {});
    } else if (route.name === "settings") {
      settings.show();
    } else {
      detail.show(route.id).catch(() => {});
    }
  },
});
router.start();
