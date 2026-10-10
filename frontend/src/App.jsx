// アプリ本体(UI v2 Phase 6)。Reactがマウントの起点となり、シェル(ヘッダー・
// tabbar・toast・appDialog・トークンパネル)はコンポーネント+コンテキスト、
// hashルートはrouter.jsのparseHash契約をそのまま消費して画面を切り替える。
// 01ホーム+02読み解き確認(作業3)・03待機中/一覧/04候補/05成立+チャット(作業4)は
// Reactコンポーネント(01↔02のフォームstateをAppで1つ共有)。設定/オンボーディングは
// 移行期のため旧DOM配線モジュールを条件マウントのブリッジで稼働させ、作業5で
// コンポーネント化する。
// 意図文言をログに出さない(01 §21)。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "./api/client.js";
import { createAppState } from "./appState.js";
import { parseHash } from "./router.js";
import { createFormState } from "./intent/state.js";
import { HomeScreen } from "./intent/HomeScreen.jsx";
import { ConfirmScreen } from "./intent/ConfirmScreen.jsx";
import { createNotice } from "./latch/notice.js";
import { createPermissionControl } from "./latch/permission.js";
import { createBlocksSection } from "./latch/blocks.js";
import { createConsent } from "./latch/consent.js";
import { StandbyScreen } from "./latch/StandbyScreen.jsx";
import { ListsScreen } from "./latch/ListsScreen.jsx";
import { DetailScreen } from "./latch/DetailScreen.jsx";
import { ChromeProvider, useChrome, ToastArea, AppDialog } from "./ui/chrome.jsx";
import { Header } from "./ui/Header.jsx";
import { Tabbar } from "./ui/Tabbar.jsx";
import { TokenPanel } from "./ui/TokenPanel.jsx";
import {
  SettingsScreenInner,
  OnboardingScreenInner,
} from "./ui/skeletons.jsx";

// ルート→topnav/tabbarの強調先。latch(詳細)は提案姿なら候補・成立姿なら成立と
// 実態で変わるため、詳細側(移行期はdetail.js・将来はDetailScreen)が差し替える
const NAV_BY_ROUTE = {
  home: "deposit",
  confirm: "deposit", // 02は「預ける」流れの中(Phase 2)
  standby: "standby",
  proposals: "proposals", // D-11簡易一覧
  matched: "matched", // D-11簡易一覧
  latch: "proposals",
};

// 同意フローのapp横断インスタンス(422 CONSENT_REQUIREDの横断検出から呼ばれる
// notifyRequiredをclientラップへ提供するため)。client単位で1つ。
const consentInstances = new WeakMap();
const ensureConsent = (client, chrome, consentRef) => {
  if (!consentInstances.has(client)) {
    // elは後からブリッジ(設定・オンボーディング)が差し込めるようにホルダーで渡す
    const el = { onboarding: null, settings: null };
    const consent = createConsent({ client, chrome, el });
    consent.el = el;
    consentInstances.set(client, consent);
    consentRef.current = consent;
  }
  return consentInstances.get(client);
};

// アプリ全体。sessionはmain.jsが生成して注入する(試験からはスタブを注入する)。
// client/appStateは省略時に生成する(試験からはスタブを注入できる)。
// clientは422 CONSENT_REQUIREDの横断検出(誘導経路b)をラップしたもの — 全Intent
// 操作はこのcallを通るため、個別画面の変更なしで全経路を拾う。consentは
// 画面配線のあとでconsentRefへ入るため参照解決は呼び出し時。
export const App = ({ session, client: injectedClient, appState: injectedAppState }) => {
  const consentRef = useRef(null);
  const [tokenPanelVisible, setTokenPanelVisible] = useState(() => !session.hasTokens());
  const showTokenPanelRef = useRef(() => {});
  showTokenPanelRef.current = () => setTokenPanelVisible(true);

  const created = useMemo(() => {
    const baseClient = createClient({
      session,
      onSessionExpired: () => showTokenPanelRef.current(),
    });
    const client = injectedClient ?? {
      call: async (method, path, opts) => {
        try {
          return await baseClient.call(method, path, opts);
        } catch (err) {
          if (err?.status === 422 && err?.code === "CONSENT_REQUIRED") {
            consentRef.current?.notifyRequired();
          }
          throw err;
        }
      },
    };
    return { client, appState: injectedAppState ?? createAppState({ client }) };
  }, [session, injectedClient, injectedAppState]);

  const { client, appState } = created;

  return (
    <ChromeProvider>
      <AppBody
        session={session}
        client={client}
        appState={appState}
        consentRef={consentRef}
        tokenPanelVisible={tokenPanelVisible}
        hideTokenPanel={() => setTokenPanelVisible(false)}
      />
    </ChromeProvider>
  );
};

const AppBody = ({
  session,
  client,
  appState,
  consentRef,
  tokenPanelVisible,
  hideTokenPanel,
}) => {
  const { setCurrentNav, setAccountInitial } = useChrome();
  const bridgeChrome = useBridgeChrome();

  // --- hashルート(router.jsのparseHash契約をReactから消費する) ----------------
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  const route = useMemo(() => parseHash(hash), [hash]);

  // nav強調(詳細を除く)。詳細は開いたlatchの実態で詳細側が差し替える
  useEffect(() => {
    if (route.name === "latch") return;
    setCurrentNav(NAV_BY_ROUTE[route.name] ?? null);
  }, [route.name, setCurrentNav]);

  // --- 01↔02で共有するフォーム状態(React流の単一の真実) -----------------------
  const [intentState, setIntentState] = useState(createFormState());
  const intentStateRef = useRef(intentState);
  intentStateRef.current = intentState;
  // 同期待ちが必要なflow(parseFlow・saveFlow)のために、更新を即時に参照できる
  // ミラー付きのsetterにする(純関数updateは呼び出し側で組てもらう)
  const updateIntentState = useCallback((updater) => {
    const next = typeof updater === "function" ? updater(intentStateRef.current) : updater;
    intentStateRef.current = next;
    setIntentState(next);
  }, []);
  const getIntentState = useCallback(() => intentStateRef.current, []);

  // アカウントボタンは表示名のイニシャル1字(失敗時は空のまま)
  useEffect(() => {
    if (!session.hasTokens()) return;
    appState
      .ensureMe()
      .then((me) => setAccountInitial(Array.from(me.display_name ?? "?")[0] ?? "?"))
      .catch(() => {});
  }, [session, appState, setAccountInitial]);

  // --- お知らせ(移行期ブリッジ。popoverの中身は作業5でコンポーネント化) -------
  useEffect(() => {
    const list = $("#noticeList");
    const moreButton = $("#noticeMoreButton");
    const dot = $("#notificationDot");
    const button = $("#noticeButton");
    if (!list || !moreButton || !dot || !button) return;
    const notice = createNotice({ client, el: { list, moreButton, dot } });
    if (session.hasTokens()) notice.preload().catch(() => {});
    const onMore = () => notice.loadMore().catch(() => {});
    const onOpen = () => {
      // popoverを開いたことの検知(開いたときだけ取得・既読化)
      if (!$("#noticePopover").hidden) notice.open().catch(() => {});
    };
    moreButton.addEventListener("click", onMore);
    button.addEventListener("click", onOpen);
    return () => {
      moreButton.removeEventListener("click", onMore);
      button.removeEventListener("click", onOpen);
    };
  }, [client, session]);

  // 02の「書き直す」→01でcomposerへフォーカス(hash遷移後に当てる)
  useEffect(() => {
    const onRewriteClick = (event) => {
      if (event.target.closest('[data-action="rewrite"]')) {
        window.setTimeout(() => {
          const el = document.getElementById("intentText");
          if (el) {
            el.focus();
            el.select();
          }
        }, 0);
      }
    };
    document.addEventListener("click", onRewriteClick);
    return () => document.removeEventListener("click", onRewriteClick);
  }, []);

  // トークン接続済みで起動した場合も未同意なら誘導
  useEffect(() => {
    if (!session.hasTokens()) return;
    ensureConsent(client, bridgeChrome, consentRef)
      .ensureGuidance()
      .catch(() => {});
  }, [session, client, bridgeChrome, consentRef]);

  return (
    <>
      <a className="skip" href="#main">本文へスキップ</a>

      <Header />

      <TokenPanel
        session={session}
        visible={tokenPanelVisible}
        onHide={hideTokenPanel}
        onConnected={() => {
          consentRef.current?.ensureGuidance().catch(() => {});
        }}
      />

      <main id="main" tabIndex={-1}>
        {route.name === "home" && (
          <HomeScreen client={client} state={intentState} getState={getIntentState} setState={updateIntentState} />
        )}
        {route.name === "confirm" && (
          <ConfirmScreen
            key={route.id ?? "new"}
            client={client}
            state={intentState}
            getState={getIntentState}
            setState={updateIntentState}
            intentId={route.id}
          />
        )}
        {route.name === "standby" && <StandbyScreen client={client} />}
        {route.name === "proposals" && <ListsScreen client={client} mode="proposals" />}
        {route.name === "matched" && <ListsScreen client={client} mode="matched" />}
        {route.name === "latch" && <DetailScreen client={client} appState={appState} latchId={route.id} />}
        {route.name === "settings" && <SettingsBridge client={client} consentRef={consentRef} />}
        {route.name === "onboarding" && <OnboardingBridge client={client} consentRef={consentRef} />}
      </main>

      <ToastArea />

      <AppDialog />
      {/* ブロック解除の確認dialog(UI v2 Phase 5)。中身は移行期blocks.jsが構成する */}
      <dialog className="dlg" id="unblockDialog" aria-labelledby="unblockTitle"></dialog>

      <Tabbar />
    </>
  );
};

const $ = (selector) => document.querySelector(selector);

// 移行期ブリッジ用: 旧DOM配線モジュールへ渡すchrome相当(安定の関数だけを含む)。
const useBridgeChrome = () => {
  const { showToast, openAppDialog, setCurrentNav, setCounts } = useChrome();
  return useMemo(
    () => ({ showToast, openAppDialog, setCurrentNav, setCounts }),
    [showToast, openAppDialog, setCurrentNav, setCounts],
  );
};
// --- 設定(移行期ブリッジ・作業5でコンポーネント化) -----------------------------
const SettingsBridge = ({ client, consentRef }) => {
  const chrome = useBridgeChrome();
  const ref = useRef(null);
  useEffect(() => {
    const root = ref.current;
    const consent = ensureConsent(client, chrome, consentRef);
    consent.el.settings = root.querySelector("#consentControl");
    const permissionControl = createPermissionControl({
      notificationApi: typeof Notification !== "undefined" ? Notification : undefined,
      mount: root.querySelector("#permissionControl"),
    });
    permissionControl.render();
    const blocks = createBlocksSection({
      client,
      el: { list: root.querySelector("#blockList"), moreButton: root.querySelector("#blockMoreButton") },
      dialog: $("#unblockDialog"),
    });
    const onMore = () => blocks.loadMore().catch(() => {});
    root.querySelector("#blockMoreButton").addEventListener("click", onMore);
    blocks.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
    consent.load().catch(() => {}); // 第3セクション(LLM送信の同意)
    return () => {
      root.querySelector("#blockMoreButton").removeEventListener("click", onMore);
    };
  }, [client, chrome, consentRef]);
  return (
    <section className="page-main page-main--settings" id="settingsScreen" ref={ref}>
      <SettingsScreenInner />
    </section>
  );
};

// --- オンボーディング(移行期ブリッジ・作業5でコンポーネント化) -----------------
const OnboardingBridge = ({ client, consentRef }) => {
  const chrome = useBridgeChrome();
  const ref = useRef(null);
  useEffect(() => {
    const consent = ensureConsent(client, chrome, consentRef);
    consent.el.onboarding = ref.current.querySelector("#consentOnboarding");
    consent.show().catch(() => {});
  }, [client, chrome, consentRef]);
  return (
    <section className="page-main page-main--settings" id="onboardingScreen" ref={ref}>
      <OnboardingScreenInner />
    </section>
  );
};
