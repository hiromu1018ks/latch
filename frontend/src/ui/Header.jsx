// ヘッダー(UI v2 Phase 6・React版)。旧index.htmlの<header class="hdr">を
// そのままJSXへ写し、topnav/tabbar共通のdata-nav強調と件数バッジを
// chromeコンテキストの状態から描く。popover(お知らせ・アカウント)の開閉は
// 旧chrome.jsと同じ挙動(外側クリックで閉じる・Escで閉じてボタンへフォーカス
// を戻す)。デザイン値はstyles.cssのままで変更しない。
import { useEffect, useRef, useState } from "react";
import { Icon, Logo } from "./Icon.jsx";
import { useChrome } from "./chrome.jsx";

const NAV_ITEMS = [
  { name: "deposit", href: "#/", label: "預ける" },
  { name: "standby", href: "#/standby", label: "待機中", count: "standby", tone: "stb" },
  { name: "proposals", href: "#/proposals", label: "候補", count: "proposals", tone: "acc" },
  { name: "matched", href: "#/matched", label: "成立", count: "matched", tone: "acc" },
];

// popover(お知らせ・アカウント)共通の開閉。1つを開くとほかを閉じる・
// 外側クリックで閉じる・Escで閉じて起動ボタンへフォーカスを戻す(a11y・
// UI v2 Phase 5の挙動を踏襲)
const usePopovers = () => {
  const [openName, setOpenName] = useState(null);
  const buttons = useRef({});

  useEffect(() => {
    const onDocumentClick = () => setOpenName(null);
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      setOpenName((current) => {
        if (current != null) buttons.current[current]?.focus();
        return null;
      });
    };
    document.addEventListener("click", onDocumentClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("click", onDocumentClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  const toggle = (name) => {
    setOpenName((current) => (current === name ? null : name));
  };

  return { openName, toggle, registerButton: (name, el) => { buttons.current[name] = el; } };
};

const CountPill = ({ count, tone }) => {
  const { counts } = useChrome();
  const n = counts[count] ?? 0;
  return (
    <span className={`count-pill ${tone}`} data-count={count} hidden={!(n > 0)}>
      {n > 0 ? n : 0}
      <span className="vh">件</span>
    </span>
  );
};

export const Header = () => {
  const { currentNav, accountInitial } = useChrome();
  const { openName, toggle, registerButton } = usePopovers();

  return (
    <header className="hdr">
      <a className="brand" href="#/" aria-label="LATCH ホーム">
        <Logo size={30} />
        <span className="wordmark">LATCH</span>
      </a>
      <nav className="topnav" aria-label="メインメニュー">
        {NAV_ITEMS.map((item) => (
          <a
            key={item.name}
            className="navlink"
            data-nav={item.name}
            href={item.href}
            aria-current={currentNav === item.name ? "page" : undefined}
          >
            {item.label}
            {item.count ? <CountPill count={item.count} tone={item.tone} /> : null}
          </a>
        ))}
      </nav>
      <div className="hdr-actions">
        <div className="popover-wrap">
          <button
            type="button"
            className="ghost icon-round"
            id="noticeButton"
            aria-label="お知らせ"
            aria-haspopup="true"
            aria-expanded={openName === "notice"}
            aria-controls="noticePopover"
            ref={(el) => registerButton("notice", el)}
            onClick={(event) => {
              event.stopPropagation();
              toggle("notice");
            }}
          >
            <Icon name="bell" size={20} />
            <NotificationDot />
          </button>
          <div className="popover notification-popover" id="noticePopover" role="status" hidden={openName !== "notice"} onClick={(event) => event.stopPropagation()}>
            <p className="popover-kicker">お知らせ</p>
            <div className="notice-list" id="noticeList"></div>
            <button className="ghost notice-more" id="noticeMoreButton" type="button" hidden>もっと見る</button>
          </div>
        </div>
        <div className="popover-wrap">
          <button
            type="button"
            className="account-button"
            id="accountButton"
            aria-label="アカウント"
            aria-haspopup="true"
            aria-expanded={openName === "account"}
            aria-controls="accountPopover"
            ref={(el) => registerButton("account", el)}
            onClick={(event) => {
              event.stopPropagation();
              toggle("account");
            }}
          >
            {accountInitial}
          </button>
          <div className="popover account-popover" id="accountPopover" hidden={openName !== "account"} onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              id="settingsButton"
              onClick={() => {
                // popoverを閉じて設定画面へ(旧main.jsと同じ操作)
                location.hash = "#/settings";
                setOpenName(null);
              }}
            >
              設定
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};

// 未読ドット(お知らせの未読があるときだけ点く)。点灯はNoticeDotBridgeが
// DOM操作で行う(お知らせ本体は後続コミットでReact化 — Phase 6 作業5)。
const NotificationDot = () => <span className="notification-dot" id="notificationDot" hidden></span>;
