// アプリ本体(UI v2 Phase 6)。Reactがマウントの起点となり、シェル(ヘッダー・
// tabbar・toast・appDialog・トークンパネル)はコンポーネント+コンテキストで
// 描く。画面本体は移行期のため旧DOM配線モジュール(intent/screen.js等)を
// スケルトン要素にブリッジして稼働させ、以降の作業で順次コンポーネント化する
// (01・02=作業3、03/一覧/04/05=作業4、横断=作業5)。
// 意図文言をログに出さない(01 §21)。
import { useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "./api/client.js";
import { createAppState } from "./appState.js";
import { createRouter } from "./router.js";
import { createFormState } from "./intent/state.js";
import { initIntentScreen } from "./intent/screen.js";
import { initConfirmScreen } from "./intent/confirm.js";
import { createHome } from "./latch/home.js";
import { createDetail } from "./latch/detail.js";
import { createStandby } from "./latch/standby.js";
import { createNotice } from "./latch/notice.js";
import { createLatchList } from "./latch/lists.js"; // D-11簡易一覧(候補・成立)
import { createPermissionControl } from "./latch/permission.js";
import { createBlocksSection } from "./latch/blocks.js";
import { createSettings } from "./latch/settings.js";
import { createSafetyFlow } from "./latch/safety.js";
import { createConsent } from "./latch/consent.js";
import { ChromeProvider, useChrome, ToastArea, AppDialog } from "./ui/chrome.jsx";
import { Header } from "./ui/Header.jsx";
import { Tabbar } from "./ui/Tabbar.jsx";
import { TokenPanel } from "./ui/TokenPanel.jsx";
import {
  HomeScreenSkeleton,
  ConfirmScreenSkeleton,
  StandbyScreenSkeleton,
  ProposalsScreenSkeleton,
  MatchedScreenSkeleton,
  SettingsScreenSkeleton,
  OnboardingScreenSkeleton,
} from "./ui/skeletons.jsx";

const $ = (selector) => document.querySelector(selector);

// アプリ全体。sessionはmain.jsが生成して注入する(試験からはスタブを注入する)。
// client/appStateは省略時に生成する(試験からはスタブを注入できる)。
// clientは422 CONSENT_REQUIREDの横断検出(誘導経路b)をラップしたもの — 全Intent
// 操作はこのcallを通るため、個別画面の変更なしで全経路を拾う。consentは
// 画面配線のあとでconsentRefへ入るため参照解決は呼び出し時。
export const App = ({ session, client: injectedClient, appState: injectedAppState }) => {
  const consentRef = useRef(null);
  const showTokenPanelRef = useRef(() => {});
  showTokenPanelRef.current = () => setTokenPanelVisible(true);
  const [tokenPanelVisible, setTokenPanelVisible] = useState(() => !session.hasTokens());

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
  const chromeValue = useChrome();
  // 配線(1回きりのeffect)からは常に最新のchrome APIを触れるようにする
  const chromeRef = useRef(chromeValue);
  chromeRef.current = chromeValue;

  // アカウントボタンは表示名のイニシャル1字(失敗時は空のまま)
  useEffect(() => {
    if (!session.hasTokens()) return;
    appState
      .ensureMe()
      .then((me) => chromeRef.current.setAccountInitial(Array.from(me.display_name ?? "?")[0] ?? "?"))
      .catch(() => {});
  }, [session, appState]);

  // --- 画面配線(移行期ブリッジ。旧main.jsのロジックをここへ移した) ------------
  useEffect(() => {
    const chrome = chromeRef.current;

    // 01入力+02確認(state共有・UI v2 Phase 2)
    const intentState = createFormState();
    const intentScreen = initIntentScreen({ client, chrome, state: intentState });
    const confirmScreen = initConfirmScreen({ client, chrome, state: intentState });

    // 02の「書き直す」→01でcomposerへフォーカス(hash遷移後に当てる)
    const onRewriteClick = (event) => {
      if (event.target.closest('[data-action="rewrite"]')) {
        window.setTimeout(() => intentScreen.focusComposer(), 0);
      }
    };
    document.addEventListener("click", onRewriteClick);

    // ホーム・詳細・03待機中・通報・お知らせ・設定
    const home = createHome({
      client,
      el: {
        intentCards: $("#intentCards"),
        standbyLine: $("#standbyLine"),
        waitSection: $("#waitSection"),
      },
      onCounts: (partial) => chrome.setCounts(partial), // 待機中・候補・成立バッジ
    });

    // 03待機中(UI v2 Phase 3): 一覧・取り下げ(DELETE)・タイマー・候補トースト
    const standby = createStandby({
      client,
      chrome,
      root: $("#standbyScreen"),
      onCounts: (partial) => chrome.setCounts(partial),
    });

    // 候補・成立の簡易一覧(D-11・UI v2 Phase 5): topnav/tabbarのタブに対応
    const proposalsList = createLatchList({
      client,
      root: $("#proposalsScreen"),
      mode: "proposals",
      onCounts: (partial) => chrome.setCounts(partial),
    });
    const matchedList = createLatchList({
      client,
      root: $("#matchedScreen"),
      mode: "matched",
      onCounts: (partial) => chrome.setCounts(partial),
    });
    $("#proposalsScreen [data-list-more]").addEventListener("click", () =>
      proposalsList.loadMore().catch(() => {}),
    );
    $("#matchedScreen [data-list-more]").addEventListener("click", () =>
      matchedList.loadMore().catch(() => {}),
    );

    // お知らせ(ws-8): 起動時1回のpreloadでドット判定
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
    // popoverを開いたことの検知(開いたときだけ取得・既読化)
    $("#noticeButton").addEventListener("click", () => {
      if (!$("#noticePopover").hidden) notice.open().catch(() => {});
    });

    // 通報・ブロックの統合dialog(UI v2 Phase 4・D-09)。ブロック成功は詳細再取得
    const safetyFlow = createSafetyFlow({
      client,
      dialog: $("#safetyDialog"),
      onBlocked: () => detail.refresh(),
    });
    const detail = createDetail({
      client,
      appState,
      chrome,
      root: $("#detailScreen"),
      safety: safetyFlow,
    });

    // 設定画面: 通知許可(DI)+ブロック管理(解除は<dialog>)
    const permissionControl = createPermissionControl({
      notificationApi: typeof Notification !== "undefined" ? Notification : undefined,
      mount: $("#permissionControl"),
    });
    const blocks = createBlocksSection({
      client,
      el: { list: $("#blockList"), moreButton: $("#blockMoreButton") },
      dialog: $("#unblockDialog"),
    });
    $("#blockMoreButton").addEventListener("click", () => blocks.loadMore().catch(() => {}));
    const consent = createConsent({
      client,
      chrome,
      el: { onboarding: $("#consentOnboarding"), settings: $("#consentControl") },
    });
    consentRef.current = consent;
    const settings = createSettings({ permissionControl, blocks, consent });

    // ルート→topnav/tabbarの強調先(詳細=開いた latch の状態でdetail側が差し替える)
    const NAV_BY_ROUTE = {
      home: "deposit",
      confirm: "deposit", // 02は「預ける」流れの中(Phase 2)
      standby: "standby",
      proposals: "proposals", // D-11簡易一覧
      matched: "matched", // D-11簡易一覧
      latch: "proposals", // 詳細は提案姿なら候補・成立姿なら成立(detail側で更新)
    };

    const router = createRouter({
      screens: {
        home: $("#homeScreen"),
        confirm: $("#confirmScreen"), // UI v2 02(Phase 2で実装)
        standby: $("#standbyScreen"), // UI v2 03(Phase 3で実装)
        proposals: $("#proposalsScreen"), // D-11簡易一覧(UI v2 Phase 5)
        matched: $("#matchedScreen"), // D-11簡易一覧(UI v2 Phase 5)
        latch: $("#detailScreen"),
        settings: $("#settingsScreen"),
        onboarding: $("#onboardingScreen"),
      },
      onRoute: (route) => {
        detail.flush(); // 画面離脱時の未送信見送り(04 D-07)+タイマー停止
        if (route.name !== "standby") standby.stop(); // 03の1秒タイマー
        chrome.setCurrentNav(NAV_BY_ROUTE[route.name] ?? null);
        if (route.name === "home") {
          intentScreen.refresh(); // 02での行編集を01のchips・CTAへ反映(再訪時)
          home.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
          home.loadIntents().catch(() => {});
        } else if (route.name === "confirm") {
          confirmScreen.enter(route.id); // idあり=03「条件を直す」の02編集モード
        } else if (route.name === "standby") {
          standby.show().catch(() => {}); // ?deposited=1は03側で読む(着地状態)
        } else if (route.name === "proposals") {
          proposalsList.show().catch(() => {});
        } else if (route.name === "matched") {
          matchedList.show().catch(() => {});
        } else if (route.name === "settings") {
          settings.show();
        } else if (route.name === "onboarding") {
          consent.show().catch(() => {});
        } else if (route.name === "latch") {
          detail.show(route.id).catch(() => {});
        }
      },
    });
    router.start();
    // トークン接続済みで起動した場合も未同意なら誘導
    if (session.hasTokens()) consent.ensureGuidance().catch(() => {});

    return () => {
      router.stop();
      document.removeEventListener("click", onRewriteClick);
      standby.stop();
      detail.flush();
      consentRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <a className="skip" href="#main">本文へスキップ</a>

      <Header />

      <TokenPanel
        session={session}
        visible={tokenPanelVisible}
        onHide={hideTokenPanel}
        onConnected={onTokenConnected(consentRef)}
      />

      <main id="main" tabIndex={-1}>
        <HomeScreenSkeleton />
        <ConfirmScreenSkeleton />
        <StandbyScreenSkeleton />
        <ProposalsScreenSkeleton />
        <MatchedScreenSkeleton />
        <section className="detail-view" id="detailScreen" hidden></section>
        <SettingsScreenSkeleton />
        <OnboardingScreenSkeleton />
      </main>

      <ToastArea />

      <AppDialog />
      {/* 通報・ブロックの統合dialog(UI v2 Phase 4・D-09)。中身は移行期safety.jsが構成する */}
      <dialog className="dlg" id="safetyDialog" aria-label="通報・ブロック"></dialog>
      {/* ブロック解除の確認dialog(UI v2 Phase 5)。中身は移行期blocks.jsが構成する */}
      <dialog className="dlg" id="unblockDialog" aria-labelledby="unblockTitle"></dialog>

      <Tabbar />
    </>
  );
};

// onConnected: トークン接続後の未同意誘導(旧main.jsの(a))
const onTokenConnected = (consentRef) => () => {
  consentRef.current?.ensureGuidance().catch(() => {});
};
