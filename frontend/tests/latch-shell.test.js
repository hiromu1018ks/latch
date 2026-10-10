import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// happy-dom環境は import.meta.url を http スキームへ書き換えるため
// process.cwd()(= npm test 実行時の frontend/)基準で解決する(smoke.test.jsと同型)
const html = () => readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const mainJs = () => readFileSync(resolve(process.cwd(), "src/main.js"), "utf8");
const chromeJs = () => readFileSync(resolve(process.cwd(), "src/ui/chrome.js"), "utf8");
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("画面構成(UI v2 Phase 2・01ホーム+02読み解き確認)", () => {
  it("画面section構造: homeScreen(01)・confirmScreen(02)・standbyプレースホルダ・detail", () => {
    expect(html()).toContain('id="homeScreen"');
    expect(html()).toContain('id="confirmScreen"');
    expect(html()).toContain('id="standbyScreen"'); // UI v2 03(Phase 3)
    expect(html()).toContain('id="detailScreen"');
    // 旧workspace(2カラム入力+預け方サイドバー)は廃止
    expect(html()).not.toContain('class="workspace"');
    expect(html()).not.toContain("candidateList");
    expect(html()).not.toContain("matchedList");
  });

  it("新ヘッダー: ロゴSVG+wordmark・topnav4項目(data-nav)・通知/アカウント", () => {
    expect(html()).toContain('class="hdr"');
    expect(html()).toContain('class="wordmark"');
    expect(html()).toMatch(/<svg[^>]*viewBox="0 0 40 40"/); // LATCHロゴ(左右2弧)
    for (const nav of ["deposit", "standby", "proposals", "matched"]) {
      expect(html()).toContain(`data-nav="${nav}"`);
    }
    expect(html()).toContain('id="noticeButton"');
    expect(html()).toContain('id="accountButton"');
  });

  it("モバイルtabbar(4タブ)と件数バッジ・topnavにも同一名のバッジ", () => {
    expect(html()).toContain('class="tabbar"');
    expect(html()).toContain('class="tab"');
    expect(html()).toContain('class="tb stb" data-count="standby"');
    expect(html()).toContain('class="count-pill stb" data-count="standby"');
    expect(html()).toContain('class="count-pill acc" data-count="proposals"');
    expect(html()).toContain('class="count-pill acc" data-count="matched"');
  });

  it("右上toast(toast-pos)とwordmarkのhrefは#/ ・通報・ブロックの統合dialog(safetyDialog)がある", () => {
    expect(html()).toContain('class="toast-pos" id="toast"');
    expect(html()).toContain('href="#/"');
    // UI v2 Phase 4(D-09): 旧のreportModal/blockModalは統合dialogへ
    expect(html()).toContain('id="safetyDialog"');
    expect(html()).not.toContain('id="reportModal"');
    expect(html()).not.toContain('id="blockModal"');
  });

  it("main.jsはscreen.js・confirm.jsとrouter.jsをimportして画面群を配線する", () => {
    expect(mainJs()).toContain('from "./intent/screen.js"');
    expect(mainJs()).toContain('from "./intent/confirm.js"');
    expect(mainJs()).toContain('from "./router.js"');
    expect(mainJs()).toContain('from "./latch/home.js"');
    expect(mainJs()).toContain('from "./latch/detail.js"');
    expect(mainJs()).toContain('from "./latch/safety.js"'); // UI v2 Phase 4(統合dialog)
    expect(mainJs()).not.toContain('from "./latch/report.js"');
    expect(mainJs()).not.toContain('from "./latch/blockFlow.js"');
    expect(mainJs()).toContain('from "./appState.js"');
    expect(mainJs()).toContain('confirm: $("#confirmScreen")');
    expect(mainJs()).toContain('standby: $("#standbyScreen")');
    expect(mainJs()).toContain("chrome.setCurrentNav"); // aria-current
    expect(mainJs()).toContain("chrome.setCounts"); // バッジ件数
    expect(mainJs()).toContain("createFormState"); // 01↔02で共有するフォーム状態
    expect(mainJs()).toContain("confirmScreen.enter(route.id)"); // #/confirmで02を初期化(idあり=編集モード)
  });

  it("chrome.jsはバッジ・aria-current・toast・dialog制御を持ち旧モーダルを持たない", () => {
    expect(chromeJs()).toContain("setCounts");
    expect(chromeJs()).toContain("setCurrentNav");
    expect(chromeJs()).toContain("showToast");
    expect(chromeJs()).toContain("openAppDialog");
    expect(chromeJs()).not.toContain("openModal");
    expect(chromeJs()).not.toContain("closeModal");
    expect(chromeJs()).not.toContain("data-theme");
    expect(chromeJs()).not.toContain("latch-theme");
  });

  it("styles.cssに01・02の画面クラスと共通部品がある", () => {
    for (const className of [
      // 01(プロトタイプ01)
      ".composer", ".chip", ".example-chip", ".intent-card", ".intent-quote",
      ".intent-tag", ".intent-pill", ".beacon", ".hero-title",
      // 02(プロトタイプ02)
      ".steps", ".words-card", ".privacy-card", ".slots-card", ".slot-row",
      ".slot-value", ".people-box", ".seg", ".extra-chip", ".deposit-button",
      // 共通部品
      ".tabbar", ".dlg", ".toast-pos", ".count-pill", ".ghost", ".cta", ".danger", ".lift",
    ]) {
      expect(css()).toContain(className);
    }
  });

  it("composerのfocus-within glowと02の行アニメーション(reduced-motionで停止)", () => {
    expect(css()).toContain(".composer:focus-within");
    expect(css()).toContain("@keyframes rowIn");
    expect(css()).toContain("@keyframes chipIn");
    expect(css()).toContain("@keyframes beacon");
  });

  it("tokenPanel等の既存構造は温存される", () => {
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "toast", "noticePopover"]) {
      expect(html()).toContain(`id="${id}"`);
    }
  });
});
