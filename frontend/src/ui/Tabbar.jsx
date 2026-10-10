// モバイルtabbar(UI v2 Phase 6・React版)。旧index.htmlの<nav class="tabbar">
// をそのままJSXへ写す。4タブ・件数バッジ(tb stb/tb acc)・aria-currentは
// chromeコンテキストの状態から描く。safe-areaのpaddingはstyles.cssのまま。
import { useChrome } from "./chrome.jsx";

// タブのアイコンはプロトタイプ(v2共通<style>)のSVGをそのまま写す。
// icons.jsの表にない専用形(円+文字・月・ロゴ弧・吹き出し)はここに置く
const TabDepositIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"></circle><path d="M12 8.5v7M8.5 12h7"></path></svg>
);
const TabStandbyIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"></path></svg>
);
const TabProposalsIcon = () => (
  <svg width="22" height="22" viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" aria-hidden="true"><path d="M19 5 A15 15 0 0 0 19 35"></path><path d="M22 5 A15 15 0 0 1 22 35"></path></svg>
);
const TabMatchedIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 5.5h14A1.5 1.5 0 0 1 20.5 7v8.5A1.5 1.5 0 0 1 19 17h-7l-4 3.5V17H5a1.5 1.5 0 0 1-1.5-1.5V7A1.5 1.5 0 0 1 5 5.5z"></path></svg>
);

const TAB_ITEMS_FINAL = [
  { name: "deposit", href: "#/", label: "預ける", IconC: TabDepositIcon },
  { name: "standby", href: "#/standby", label: "待機中", IconC: TabStandbyIcon, count: "standby", tone: "stb" },
  { name: "proposals", href: "#/proposals", label: "候補", IconC: TabProposalsIcon, count: "proposals", tone: "acc" },
  { name: "matched", href: "#/matched", label: "成立", IconC: TabMatchedIcon, count: "matched", tone: "acc" },
];

const TabBadge = ({ count, tone }) => {
  const { counts } = useChrome();
  const n = counts[count] ?? 0;
  return (
    <span className={`tb ${tone}`} data-count={count} hidden={!(n > 0)}>
      {n > 0 ? n : 0}
      <span className="vh">件</span>
    </span>
  );
};

export const Tabbar = () => {
  const { currentNav } = useChrome();
  return (
    <nav className="tabbar" aria-label="メインメニュー">
      {TAB_ITEMS_FINAL.map(({ name, href, label, IconC, count, tone }) => (
        <a
          key={name}
          className="tab"
          data-nav={name}
          href={href}
          aria-current={currentNav === name ? "page" : undefined}
        >
          <IconC />
          <span>{label}</span>
          {count ? <TabBadge count={count} tone={tone} /> : null}
        </a>
      ))}
    </nav>
  );
};
