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

  it("prototypeの主要IDを維持する(実装基準・00 運用ルール6)", () => {
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
      "themeButton",
    ];
    for (const id of ids) {
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("スタイルとアセットが参照される", () => {
    expect(html()).toContain('href="/styles.css"');
    // テクスチャはstyles.cssから参照される(prototype実態に合わせた検証)
    expect(css()).toContain("paper-milk.png");
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
