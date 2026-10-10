import { describe, expect, it } from "vitest";
import { act } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderApp, renderConfirm } from "./helpers.jsx";
import { applyParseResult, createFormState } from "../src/intent/state.js";

// UI v2 Phase 6(React載せ替え)からシェルとスケルトンはsrc/App.jsxが描くため、
// index.htmlの本文テストは「マウント点があること」へ、構成のテストは
// App描画のDOMへの試験へ置き換えた(意図=文言・ID・a11yの踏襲)。
const html = () => readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("frontend雛形", () => {
  it("script type=module で /src/main.jsx を読み・#rootへReactでマウントする", () => {
    expect(html()).toContain('src="/src/main.jsx"');
    expect(html()).toContain('<div id="root"></div>');
  });

  it("スタイルが参照され、v2トークン「夜と翡翠」が定義される", () => {
    expect(html()).toContain('href="/styles.css"');
    expect(css()).toContain("--acc:#5FD4AE");
    expect(css()).toContain("--bg:#0B0C12");
  });
});

describe("シェル+スケルトンの構成(App描画)", () => {
  const setup = () => {
    const utils = renderApp();
    return utils;
  };

  it("01ホームの主要ID(UI v2 Phase 2・composer+chips+例文+待機中の意思)", () => {
    setup();
    const ids = [
      "homeScreen",
      "intentText",
      "readStatus",
      "readChips",
      "parseError",
      "readButton",
      "draftButton",
      "ctaHelp",
      "exampleChips",
      "intentCards",
      "standbyLine",
      "waitSection",
      "toast",
    ];
    for (const id of ids) {
      expect(document.getElementById(id)).not.toBeNull();
    }
  });

  it("02読み解き確認の主要ID(3ステップ・あなたの言葉・5行・seg・期限select)", () => {
    renderConfirm({ state: createFormState() });
    const ids = [
      "confirmScreen",
      "wordsText",
      "confirmRows",
      "extraChips",
      "expiry",
      "expiryLabel",
      "slotSummary",
      "depositButton",
      "depositHelp",
      "confirmError",
    ];
    for (const id of ids) {
      expect(document.getElementById(id)).not.toBeNull();
    }
  });

  it("03待機中・一覧・詳細・設定・オンボーディングのsectionをルートごとに組む", async () => {
    setup();
    const go = async (hash) => {
      window.location.hash = hash;
      window.dispatchEvent(new Event("hashchange"));
      await act(async () => {});
    };
    for (const [hash, id] of [
      ["#/standby", "standbyScreen"],
      ["#/proposals", "proposalsScreen"],
      ["#/matched", "matchedScreen"],
      ["#/settings", "settingsScreen"],
      ["#/onboarding", "onboardingScreen"],
      ["#/latches/11111111-1111-4111-8111-111111111111", "detailScreen"],
    ]) {
      await go(hash);
      expect(document.getElementById(id)).not.toBeNull();
    }
    // appDialog・解除dialogはシェル側に常在・統合dialogは詳細(DetailScreen)が担う
    for (const id of ["appDialog", "unblockDialog"]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
    await go("#/latches/11111111-1111-4111-8111-111111111111");
    expect(document.getElementById("safetyDialog")).not.toBeNull();
  });
});

describe("01・02の構成(プロトタイプが正)", () => {
  it("ヒーロー「どんな条件なら、動きますか。」とcomposerのplaceholder", () => {
    renderApp();
    expect(document.querySelector(".hero-title").innerHTML).toContain(
      "どんな条件なら、<br>動きますか。",
    );
    expect(document.querySelector("#intentText").placeholder).toBe(
      "例：今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい",
    );
  });

  it("textareaは300字上限を維持し文字数カウンタは存在しない(プロトタイプ01)", () => {
    renderApp();
    const textarea = document.querySelector("#intentText");
    expect(textarea.maxLength).toBe(300);
    expect(document.body.textContent).not.toContain("characterCount");
  });

  it("読み取りchipsはaria-live=polite・未読み取りガイドの2状態", () => {
    renderApp();
    expect(document.querySelector("#readStatus").getAttribute("aria-live")).toBe("polite");
    expect(document.querySelector("[data-guide]")).not.toBeNull();
    expect(document.querySelector("[data-noread]")).not.toBeNull();
    expect(document.body.textContent).toContain("まだ条件を読み取れません");
  });

  it("例文4種のchips枠と「読み解く」CTA", () => {
    renderApp();
    expect(document.body.textContent).toContain("こんな預け方もできます");
    expect(document.getElementById("exampleChips")).not.toBeNull();
    expect(document.getElementById("readButton")).not.toBeNull();
    expect(document.querySelector("#readButton .cta-label").textContent).toBe("読み解く");
  });

  it("02は3ステップインジケータと「こう読み解きました。」", () => {
    renderConfirm({ state: createFormState() });
    expect(document.querySelector(".steps")).not.toBeNull();
    const text = document.body.textContent;
    expect(text).toContain("書く");
    expect(text).toContain("読み解きを確認");
    expect(text).toContain("預ける");
    expect(text).toContain("こう読み解きました。");
    expect(text).toContain("あなたの言葉");
    expect(text).toContain("公開範囲と通知");
    expect(text).toContain("読み取った条件");
    expect(document.querySelector("#depositButton .cta-label").textContent).toBe(
      "この条件で預ける",
    );
  });

  it("公開設定・通知設定のselectは存在しない(D-02: 既定値で確定送信)", () => {
    renderApp();
    expect(document.getElementById("privacy")).toBeNull();
    expect(document.getElementById("notification")).toBeNull();
  });
});

describe("本実装の構成(Task 11)", () => {
  it("トークンパネル・エラー表示の要素がある(トークン未設定で表示)", () => {
    renderApp();
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "tokenError", "parseError"]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
    expect(document.getElementById("tokenPanel").hidden).toBe(false);
  });

  it("expiry selectは初期disabled(選択肢はAPI応答から構成・02のカード内)", () => {
    renderConfirm({ state: applyParseResult(createFormState(), { category: { primary: null, secondary: null }, alcohol_involved: null, time: { start: null, end: null }, location: { name: null, radius_m: null }, budget: { max: null, currency: "JPY" }, participants: { min: null, max: null }, soft_constraints: [], negative_constraints: [], ng_unverifiable: [] }) });
    const select = document.getElementById("expiry");
    expect(select.disabled).toBe(true);
    expect(select.className).toContain("field");
  });
});

describe("UI v2シェル(Phase 1)", () => {
  it("head: theme-color・viewport-fit=cover(D-12: フォントはself-hostのみ)", () => {
    expect(html()).toContain('name="theme-color" content="#0B0C12"');
    expect(html()).toContain("viewport-fit=cover");
    expect(html()).not.toContain("fonts.googleapis.com"); // CDN不使用
    expect(css()).toContain("@fontsource/zen-old-mincho");
    expect(css()).toContain("@fontsource/zen-kaku-gothic-new");
    expect(css()).toContain("@fontsource/ibm-plex-mono");
  });

  it("テーマ切替機構は存在しない(gap分析§1)", () => {
    renderApp();
    expect(document.getElementById("themeButton")).toBeNull();
    expect(document.querySelector("[data-theme]")).toBeNull();
    expect(html()).not.toContain("latch-theme");
    expect(css()).not.toContain("paper-milk.png"); // paperテクスチャ廃止
  });

  it("Phosphorアイコン(ph-クラス)を使わない(インラインSVGへ置換)", () => {
    const { container } = renderApp();
    expect(container.innerHTML).not.toMatch(/class="ph /);
    expect(container.innerHTML).not.toMatch(/ph ph-/);
  });

  it("skip link・視覚隠し・<dialog>の土台がある", () => {
    renderApp();
    expect(document.querySelector('a.skip[href="#main"]')).not.toBeNull();
    expect(document.querySelector('a.skip').textContent).toBe("本文へスキップ");
    expect(document.getElementById("appDialog")).not.toBeNull();
    expect(css()).toContain(".vh{");
    expect(css()).toContain(".dlg::backdrop");
  });

  it("focus-visible・prefers-reduced-motion・720pxのtabbar規定", () => {
    expect(css()).toContain(":focus-visible{outline:2px solid var(--acc);outline-offset:3px}");
    expect(css()).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css()).toContain("@media (max-width:720px)");
    expect(css()).toContain("env(safe-area-inset-bottom)");
  });
});

describe("03待機中・04候補が届く(UI v2 Phase 3)", () => {
  it("03の主要フック(バッジ・見出し2行・メインカード・オーブ・ほかの意思)", async () => {
    const utils = renderApp();
    window.location.hash = "#/standby";
    window.dispatchEvent(new Event("hashchange"));
    await act(async () => {});
    for (const hook of [
      "data-sb-badge",
      "data-sb-title-a",
      "data-sb-title-b",
      "data-sb-lead",
      "data-sb-main",
      "data-sb-orb",
      "data-sb-others",
      "data-sb-drafts-section",
    ]) {
      expect(document.querySelector(`[${hook}]`)).not.toBeNull();
    }
    // 3カード(いま、LATCHがしていること)+通知プレビュー
    const text = document.body.textContent;
    expect(text).toContain("いま、LATCHがしていること");
    expect(text).toContain("照らし合わせ続ける");
    expect(text).toContain("成立し得るかだけを見る");
    expect(text).toContain("揃ったら、ひとことだけ");
    expect(text).toContain("LATCH候補があります");
    expect(text).toContain("ほかに待機中の意思");
    expect(text).toContain("新しく預ける");
    // プレースホルダは解消済み
    expect(text).not.toContain("この画面は準備中です");
  });

  it("03・04のスタイル(オーブ・タイマー・照合表・4状態・timer-low)", () => {
    for (const cls of [
      ".sb-main{",
      ".sb-timer [data-sb-timer]{",
      ".orb-chip{",
      ".ripple{",
      ".pv-grid{",
      ".pv-cards{",
      ".fit-row{",
      ".pv-decide{",
      ".pv-timer{",
      ".pv-state-card.joined{",
      ".pv-state-card.allin{",
      ".pv-state-card.declined{",
      ".reason-chip{",
      ".timer-low{color:var(--dng)",
      ".toast-link{",
    ]) {
      expect(css()).toContain(cls);
    }
  });
});
