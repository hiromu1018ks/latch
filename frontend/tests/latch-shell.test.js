import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// happy-dom環境は import.meta.url を http スキームへ書き換えるため
// process.cwd()(= npm test 実行時の frontend/)基準で解決する(smoke.test.jsと同型)
const html = () => readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const mainJs = () => readFileSync(resolve(process.cwd(), "src/main.js"), "utf8");
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("M3 ws-7 画面構造(design §2.1〜§2.2・§3)", () => {
  it("画面section構造: homeScreen(workspace+2セクション)とdetailScreen", () => {
    expect(html()).toContain('id="homeScreen"');
    expect(html()).toContain('id="detailScreen"');
    expect(html()).toContain('id="candidateList"');
    expect(html()).toContain('id="matchedList"');
    expect(html()).toContain('id="moreButton"');
    // 既存の入力画面要素はworkspace内に維持(smoke.test.jsのID群)
    expect(html()).toContain('class="workspace"');
  });

  it("Active Intent一覧: intentCountButtonとintentPopover・intentList", () => {
    expect(html()).toContain('id="intentCountButton"');
    expect(html()).toContain('id="intentPopover"');
    expect(html()).toContain('id="intentList"');
  });

  it("wordmarkのhrefは#/ ・reportModalがある", () => {
    expect(html()).toContain('href="#/"');
    expect(html()).toContain('id="reportModal"');
  });

  it("main.jsはscreen.jsとrouter.jsをimportして両画面を配線する", () => {
    expect(mainJs()).toContain('from "./intent/screen.js"');
    expect(mainJs()).toContain('from "./router.js"');
    expect(mainJs()).toContain('from "./latch/home.js"');
    expect(mainJs()).toContain('from "./latch/detail.js"');
    expect(mainJs()).toContain('from "./latch/report.js"');
    expect(mainJs()).toContain('from "./appState.js"');
  });

  it("styles.cssに新画面クラスがある(既存クラス無変更・design §3)", () => {
    for (const className of [
      ".latch-card", ".latch-section", ".chat-message", ".chat-mine",
      ".chat-theirs", ".respond-button", ".attendance-field", ".report-entry",
      ".latch-empty",
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
