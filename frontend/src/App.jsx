// アプリ本体(UI v2 Phase 6)。Reactがマウントの起点となり、シェル(ヘッダー・
// tabbar・toast・appDialog・トークンパネル)はコンポーネント+コンテキスト、
// hashルートはrouter.jsのparseHash契約をそのまま消費して画面を切り替える。
// 01ホーム+02読み解き確認(作業3)・03待機中/一覧/04候補/05成立+チャット(作業4)・
// 設定/オンボーディング(作業5)はすべてReactコンポーネント(01↔02のフォーム
// stateをAppで1つ共有)。純関数モジュール(format・state・parseFlow・highlight・
// expiry・view・texts・save)とAPIクライアント(session・client・appState)は
// フレームワーク非依存のままimportして使う。
// 意図文言をログに出さない(01 §21)。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "./api/client.js";
import { reportSessionStart } from "./api/sessionsBeacon.js";
import { createAppState } from "./appState.js";
import { parseHash } from "./router.js";
import { createFormState } from "./intent/state.js";
import { HomeScreen } from "./intent/HomeScreen.jsx";
import { ConfirmScreen } from "./intent/ConfirmScreen.jsx";
import { StandbyScreen } from "./latch/StandbyScreen.jsx";
import { ListsScreen } from "./latch/ListsScreen.jsx";
import { DetailScreen } from "./latch/DetailScreen.jsx";
import { SettingsScreen, OnboardingScreen } from "./latch/SettingsScreen.jsx";
import { ensureConsentGuidance, notifyConsentRequired } from "./latch/consentGate.js";
import { ChromeProvider, useChrome, ToastArea, AppDialog } from "./ui/chrome.jsx";
import { Header } from "./ui/Header.jsx";
import { Tabbar } from "./ui/Tabbar.jsx";
import { TokenPanel } from "./ui/TokenPanel.jsx";

// ルート→topnav/tabbarの強調先。latch(詳細)は提案姿なら候補・成立姿なら成立と
// 実態で変わるため、詳細側(DetailScreen)が差し替える
const NAV_BY_ROUTE = {
  home: "deposit",
  confirm: "deposit", // 02は「預ける」流れの中(Phase 2)
  standby: "standby",
  proposals: "proposals", // D-11簡易一覧
  matched: "matched", // D-11簡易一覧
  latch: "proposals",
};

// アプリ全体。sessionはmain.jsが生成して注入する(試験からはスタブを注入する)。
// client/appStateは省略時に生成する(試験からはスタブを注入できる)。
// clientは422 CONSENT_REQUIREDの横断検出(誘導経路b)をラップしたもの — 全Intent
// 操作はこのcallを通るため、個別画面の変更なしで全経路を拾う。
export const App = ({ session, client: injectedClient, appState: injectedAppState }) => {
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
            notifyConsentRequiredRef.current?.();
          }
          throw err;
        }
      },
    };
    return { client, appState: injectedAppState ?? createAppState({ client }) };
  }, [session, injectedClient, injectedAppState]);

  const { client, appState } = created;

  // 422検出時の誘導はchromeのトーストを伴うため、AppBodyから受け取る
  const notifyConsentRequiredRef = useRef(null);

  return (
    <ChromeProvider>
      <AppBody
        session={session}
        client={client}
        appState={appState}
        tokenPanelVisible={tokenPanelVisible}
        hideTokenPanel={() => setTokenPanelVisible(false)}
        notifyConsentRequiredRef={notifyConsentRequiredRef}
      />
    </ChromeProvider>
  );
};

const AppBody = ({
  session,
  client,
  appState,
  tokenPanelVisible,
  hideTokenPanel,
  notifyConsentRequiredRef,
}) => {
  const { setCurrentNav, setAccountInitial, showToast } = useChrome();

  // 422 CONSENT_REQUIREDの横断検出から呼ぶ(誘導経路b)
  notifyConsentRequiredRef.current = () => notifyConsentRequired(showToast);

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
  // ミラー付きのsetterにする
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

  // トークン接続済みで起動した場合も未同意なら誘導(誘導経路a)
  useEffect(() => {
    if (!session.hasTokens()) return;
    ensureConsentGuidance(client).catch(() => {});
  }, [session, client]);

  // セッション開始ビーコン(O-07・04 §2): 起動時に1回だけ(失敗は握る)。
  // トークンパネル接続直後の契機はonConnected側で送る(1マウント1回の担保)
  useEffect(() => {
    if (!session.hasTokens()) return;
    reportSessionStart({ client });
  }, [session, client]);

  return (
    <>
      <a className="skip" href="#main">本文へスキップ</a>

      <Header client={client} noticeEnabled={session.hasTokens()} />

      <TokenPanel
        session={session}
        visible={tokenPanelVisible}
        onHide={hideTokenPanel}
        onConnected={() => {
          reportSessionStart({ client }); // セッション開始(O-07・接続=新セッション)
          ensureConsentGuidance(client).catch(() => {}); // 接続後の未同意誘導(a)
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
        {route.name === "settings" && <SettingsScreen client={client} />}
        {route.name === "onboarding" && <OnboardingScreen client={client} />}
      </main>

      <ToastArea />

      <AppDialog />
      {/* ブロック解除の確認dialogは設定画面(BlocksSection)が担う */}

      <Tabbar />
    </>
  );
};
