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

  it("01ホームの主要ID(UI v2 Phase 2・composer+chips+例文+待機中の意思)", () => {
    const ids = [
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
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("02読み解き確認の主要ID(3ステップ・あなたの言葉・5行・seg・期限select)", () => {
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
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("スタイルが参照され、v2トークン「夜と翡翠」が定義される", () => {
    expect(html()).toContain('href="/styles.css"');
    expect(css()).toContain("--acc:#5FD4AE");
    expect(css()).toContain("--bg:#0B0C12");
  });
});

describe("01・02の構成(プロトタイプが正)", () => {
  it("ヒーロー「どんな条件なら、動きますか。」とcomposerのplaceholder", () => {
    expect(html()).toContain("どんな条件なら、<br>動きますか。");
    expect(html()).toContain(
      'placeholder="例：今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい"',
    );
  });

  it("textareaは300字上限を維持し文字数カウンタは存在しない(プロトタイプ01)", () => {
    expect(html()).toMatch(/<textarea id="intentText"[^>]*maxlength="300"/);
    expect(html()).not.toContain("characterCount");
  });

  it("読み取りchipsはaria-live=polite・未読み取りガイドの2状態", () => {
    expect(html()).toContain('id="readStatus" aria-live="polite"');
    expect(html()).toContain("data-guide");
    expect(html()).toContain("data-noread");
    expect(html()).toContain("まだ条件を読み取れません");
  });

  it("例文4種のchips枠と「読み解く」CTA", () => {
    expect(html()).toContain("こんな預け方もできます");
    expect(html()).toContain('id="exampleChips"');
    expect(html()).toContain('id="readButton"');
    expect(html()).toContain("<span class=\"cta-label\">読み解く</span>");
  });

  it("02は3ステップインジケータと「こう読み解きました。」", () => {
    expect(html()).toContain('class="steps"');
    expect(html()).toContain("書く");
    expect(html()).toContain("読み解きを確認");
    expect(html()).toContain("預ける");
    expect(html()).toContain("こう読み解きました。");
    expect(html()).toContain("あなたの言葉");
    expect(html()).toContain("公開範囲と通知");
    expect(html()).toContain("読み取った条件");
    expect(html()).toContain("<span class=\"cta-label\">この条件で預ける</span>");
  });

  it("公開設定・通知設定のselectは存在しない(D-02: 既定値で確定送信)", () => {
    expect(html()).not.toContain('id="privacy"');
    expect(html()).not.toContain('id="notification"');
  });

  it("旧確認モーダルは存在しない(02→03遷移に置き換わった)", () => {
    expect(html()).not.toContain("confirmationModal");
    expect(html()).not.toContain("returnButton");
    expect(html()).not.toContain("modalClose");
  });
});

describe("本実装の構成(Task 11)", () => {
  it("トークンパネル・エラー表示の要素がある", () => {
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "tokenError", "parseError"]) {
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("expiry selectは初期disabled(選択肢はAPI応答から構成・02のカード内)", () => {
    expect(html()).toMatch(/<select id="expiry" class="field" disabled><\/select>/);
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

describe("03待機中・04候補が届く(UI v2 Phase 3)", () => {
  it("03の主要フック(バッジ・見出し2行・メインカード・オーブ・ほかの意思)", () => {
    const ids = ["standbyScreen"];
    for (const id of ids) expect(html()).toContain(`id="${id}"`);
    for (const hook of [
      "data-sb-badge",
      "data-sb-title-a",
      "data-sb-title-b",
      "data-sb-lead",
      "data-sb-main",
      "data-sb-orb",
      "data-sb-others",
    ]) {
      expect(html()).toContain(hook);
    }
    // 3カード(いま、LATCHがしていること)+通知プレビュー
    expect(html()).toContain("いま、LATCHがしていること");
    expect(html()).toContain("照らし合わせ続ける");
    expect(html()).toContain("成立し得るかだけを見る");
    expect(html()).toContain("揃ったら、ひとことだけ");
    expect(html()).toContain("LATCH候補があります");
    expect(html()).toContain("ほかに待機中の意思");
    expect(html()).toContain("新しく預ける");
    // プレースホルダは解消済み
    expect(html()).not.toContain("この画面は準備中です");
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
