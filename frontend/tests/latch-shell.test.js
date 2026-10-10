import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// happy-dom環境は import.meta.url を http スキームへ書き換えるため
// process.cwd()(= npm test 実行時の frontend/)基準で解決する(smoke.test.jsと同型)
const html = () => readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const mainJs = () => readFileSync(resolve(process.cwd(), "src/main.js"), "utf8");
const chromeJs = () => readFileSync(resolve(process.cwd(), "src/ui/chrome.js"), "utf8");
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("M3 ws-7 画面構造(design §2.1〜§2.2・§3)", () => {
  it("画面section構造: homeScreen(workspace+2セクション)とdetailScreen・プレースホルダ2件", () => {
    expect(html()).toContain('id="homeScreen"');
    expect(html()).toContain('id="detailScreen"');
    expect(html()).toContain('id="confirmScreen"'); // UI v2 02
    expect(html()).toContain('id="standbyScreen"'); // UI v2 03
    expect(html()).toContain('id="candidateList"');
    expect(html()).toContain('id="matchedList"');
    expect(html()).toContain('id="moreButton"');
    // 既存の入力画面要素はworkspace内に維持(smoke.test.jsのID群)
    expect(html()).toContain('class="workspace"');
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

  it("右上toast(toast-pos)とwordmarkのhrefは#/ ・reportModalがある", () => {
    expect(html()).toContain('class="toast-pos" id="toast"');
    expect(html()).toContain('href="#/"');
    expect(html()).toContain('id="reportModal"');
  });

  it("main.jsはscreen.jsとrouter.jsをimportして画面群を配線する", () => {
    expect(mainJs()).toContain('from "./intent/screen.js"');
    expect(mainJs()).toContain('from "./router.js"');
    expect(mainJs()).toContain('from "./latch/home.js"');
    expect(mainJs()).toContain('from "./latch/detail.js"');
    expect(mainJs()).toContain('from "./latch/report.js"');
    expect(mainJs()).toContain('from "./appState.js"');
    expect(mainJs()).toContain('confirm: $("#confirmScreen")');
    expect(mainJs()).toContain('standby: $("#standbyScreen")');
    expect(mainJs()).toContain("chrome.setCurrentNav"); // aria-current
    expect(mainJs()).toContain("chrome.setCounts"); // バッジ件数
  });

  it("chrome.jsはバッジ・aria-current・toast・dialog制御を持ちテーマ切替を持たない", () => {
    expect(chromeJs()).toContain("setCounts");
    expect(chromeJs()).toContain("setCurrentNav");
    expect(chromeJs()).toContain("showToast");
    expect(chromeJs()).toContain("openAppDialog");
    expect(chromeJs()).not.toContain("data-theme");
    expect(chromeJs()).not.toContain("latch-theme");
  });

  it("styles.cssに新画面クラスがある(暫定表示+共通部品)", () => {
    for (const className of [
      ".latch-card", ".latch-section", ".chat-message", ".chat-mine",
      ".chat-theirs", ".respond-button", ".attendance-field", ".report-entry",
      ".latch-empty",
      ".tabbar", ".dlg", ".toast-pos", ".count-pill", ".ghost", ".cta", ".danger", ".lift",
    ]) {
      expect(css()).toContain(className);
    }
  });

  it("tokenPanel等の既存構造は温存される", () => {
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "toast", "confirmationModal", "noticePopover"]) {
      expect(html()).toContain(`id="${id}"`);
    }
  });
});
