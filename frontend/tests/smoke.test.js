import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// happy-dom環境は import.meta.url を http スキームへ書き換えるため
// process.cwd()(= npm test 実行時の frontend/)基準で解決する
const html = () => readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("frontend雛形", () => {
  it("script type=module で /src/main.js を読む", () => {
    expect(html()).toContain('src="/src/main.js"');
  });

  it("入力画面の主要IDを維持する(暫定表示・Phase 2で01/02へ再構成)", () => {
    const ids = [
      "intentText",
      "characterCount",
      "conditionList",
      "addConditionButton",
      "addConditionForm",
      "newConditionLabel",
      "newConditionValue",
      "cancelAdd",
      "expiry",
      "privacy",
      "notification",
      "submitButton",
      "draftButton",
      "toast",
      "confirmationModal",
      "modalClose",
      "returnButton",
    ];
    for (const id of ids) {
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("スタイルが参照され、v2トークン「夜と翡翠」が定義される", () => {
    expect(html()).toContain('href="/styles.css"');
    expect(css()).toContain("--acc:#5FD4AE");
    expect(css()).toContain("--bg:#0B0C12");
  });
});

describe("本実装の構成(Task 11)", () => {
  it("トークンパネル・催促注記・エラー表示の要素がある", () => {
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "tokenError", "requiredNote", "parseError", "globalError"]) {
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("expiry selectは初期disabled(選択肢はAPI応答から構成)", () => {
    expect(html()).toMatch(/<select id="expiry" disabled><\/select>/);
  });

  it("初期テキストは空(サンプル文言を残さない・文言対応表)", () => {
    expect(html()).toContain('id="characterCount">0 / 300');
    expect(html()).not.toContain("天文館");
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
    expect(html()).not.toContain("themeButton");
    expect(html()).not.toContain("data-theme");
    expect(html()).not.toContain("latch-theme");
    expect(css()).not.toContain("paper-milk.png"); // paperテクスチャ廃止
  });

  it("Phosphorアイコン(ph-クラス)を使わない(インラインSVGへ置換)", () => {
    expect(html()).not.toMatch(/class="ph /);
    expect(html()).not.toMatch(/ph ph-/);
  });

  it("skip link・視覚隠し・ドットグリッド・<dialog>の土台がある", () => {
    expect(html()).toContain('class="skip" href="#main"');
    expect(html()).toContain('id="appDialog"');
    expect(css()).toContain(".vh{");
    expect(css()).toContain(".ground{");
    expect(css()).toContain(".dlg::backdrop");
  });

  it("focus-visible・prefers-reduced-motion・720pxのtabbar規定", () => {
    expect(css()).toContain(":focus-visible{outline:2px solid var(--acc);outline-offset:3px}");
    expect(css()).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css()).toContain("@media (max-width:720px)");
    expect(css()).toContain("env(safe-area-inset-bottom)");
  });
});
