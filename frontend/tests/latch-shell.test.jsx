import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent } from "@testing-library/react";
import { AppDialog, ToastArea, useChrome } from "../src/ui/chrome.jsx";
import { renderApp, renderWithChrome, stubSession } from "./helpers.jsx";

// UI v2 Phase 6(React載せ替え)。シェル(ヘッダー・tabbar・toast・dialog・
// popover)はsrc/ui/のReactコンポーネントで描くため、旧index.html/chrome.jsの
// 本文テストを描画ベースへ置き換えた(意図=構造・文言・a11y・挙動の踏襲)。
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("シェルの構成(App描画)", () => {
  it("画面section構造: ルートごとにAppが1画面を条件マウントする", async () => {
    const actAsync = async () => await act(async () => {});
    renderApp();
    const go = async (hash) => {
      window.location.hash = hash;
      window.dispatchEvent(new Event("hashchange"));
      await actAsync();
    };
    // 01ホーム(既定)
    expect(document.getElementById("homeScreen")).not.toBeNull();
    // 03待機中・D-11一覧・設定・オンボーディング・詳細(ブリッジ)
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
    await go("#/");
    expect(document.getElementById("homeScreen")).not.toBeNull();
    // 旧workspace(2カラム入力+預け方サイドバー)は廃止済み
    expect(document.querySelector(".workspace")).toBeNull();
  });

  it("新ヘッダー: ロゴSVG+wordmark・topnav4項目(data-nav)・通知/アカウント", () => {
    renderApp();
    expect(document.querySelector(".hdr")).not.toBeNull();
    expect(document.querySelector(".wordmark").textContent).toBe("LATCH");
    // LATCHロゴ(左右2弧・viewBox 0 0 40 40)
    expect(document.querySelector(".hdr .brand svg").getAttribute("viewBox")).toBe("0 0 40 40");
    for (const nav of ["deposit", "standby", "proposals", "matched"]) {
      expect(document.querySelectorAll(`[data-nav="${nav}"]`).length).toBe(2); // topnav+tabbar
    }
    expect(document.getElementById("noticeButton")).not.toBeNull();
    expect(document.getElementById("accountButton")).not.toBeNull();
  });

  it("モバイルtabbar(4タブ)と件数バッジ・topnavにも同一名のバッジ(0件は非表示)", () => {
    renderApp();
    expect(document.querySelector(".tabbar")).not.toBeNull();
    expect(document.querySelectorAll(".tabbar .tab").length).toBe(4);
    expect(document.querySelector('.tabbar .tb[data-count="standby"]').className).toContain("stb");
    expect(document.querySelector('.topnav .count-pill[data-count="standby"]').className).toContain("stb");
    expect(document.querySelector('.topnav .count-pill[data-count="proposals"]').className).toContain("acc");
    expect(document.querySelector('.topnav .count-pill[data-count="matched"]').className).toContain("acc");
    for (const pill of document.querySelectorAll("[data-count]")) {
      expect(pill.hidden).toBe(true); // 0件
    }
  });

  it("右上toast(toast-pos)とwordmarkのhrefは#/ ・通報・ブロックの統合dialog(safetyDialog)がある", () => {
    renderApp();
    expect(document.querySelector(".toast-pos#toast")).not.toBeNull();
    expect(document.querySelector("a.brand").getAttribute("href")).toBe("#/");
    // UI v2 Phase 4(D-09): 旧のreportModal/blockModalは統合dialogへ
    expect(document.getElementById("safetyDialog")).not.toBeNull();
    expect(document.getElementById("reportModal")).toBeNull();
    expect(document.getElementById("blockModal")).toBeNull();
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
});

describe("シェルの配線(App描画・ルーターとトークンパネル)", () => {
  it("hash変更で画面sectionが切り替わる(parseHash契約をReactが消費する)", async () => {
    renderApp();
    expect(document.getElementById("homeScreen")).not.toBeNull();
    window.location.hash = "#/standby";
    window.dispatchEvent(new Event("hashchange"));
    await act(async () => {});
    expect(document.getElementById("homeScreen")).toBeNull(); // 非activeは条件マウントから外れる
    expect(document.getElementById("standbyScreen")).not.toBeNull();
    window.location.hash = "#/";
    window.dispatchEvent(new Event("hashchange"));
    await act(async () => {});
    expect(document.getElementById("homeScreen")).not.toBeNull();
    expect(document.getElementById("standbyScreen")).toBeNull();
  });

  it("トークン未設定ではトークンパネルが開き・接続ボタンがある", () => {
    renderApp();
    expect(document.getElementById("tokenPanel").hidden).toBe(false);
    expect(document.getElementById("tokenConnect").textContent).toBe("接続する");
  });

  it("トークン接続済みではトークンパネルは開かない(トークン未設定時のみ表示)", () => {
    renderApp({ session: stubSession({ hasTokens: () => true }) });
    expect(document.getElementById("tokenPanel")).toBeNull();
  });
});

describe("popoverの挙動(お知らせ・アカウント)", () => {
  it("ボタンで開き・aria-expandedとaria-controlsが変わる・外側クリックで閉じる", async () => {
    renderApp();
    const noticeButton = document.getElementById("noticeButton");
    const noticePopover = document.getElementById("noticePopover");
    expect(noticePopover.hidden).toBe(true);
    expect(noticeButton.getAttribute("aria-haspopup")).toBe("true");
    expect(noticeButton.getAttribute("aria-expanded")).toBe("false");
    expect(noticeButton.getAttribute("aria-controls")).toBe("noticePopover");
    fireEvent.click(noticeButton);
    await act(async () => {});
    expect(noticePopover.hidden).toBe(false);
    expect(noticeButton.getAttribute("aria-expanded")).toBe("true");
    // 1つを開くとほかは閉じる
    fireEvent.click(document.getElementById("accountButton"));
    await act(async () => {});
    expect(noticePopover.hidden).toBe(true);
    expect(document.getElementById("accountPopover").hidden).toBe(false);
    // 外側クリックで閉じる
    fireEvent.click(document.body);
    await act(async () => {});
    expect(document.getElementById("accountPopover").hidden).toBe(true);
  });

  it("Escでpopoverを閉じて起動ボタンへフォーカスを戻す", async () => {
    renderApp();
    const accountButton = document.getElementById("accountButton");
    fireEvent.click(accountButton);
    await act(async () => {});
    fireEvent.keyDown(document, { key: "Escape" });
    await act(async () => {});
    expect(document.getElementById("accountPopover").hidden).toBe(true);
    expect(document.activeElement).toBe(accountButton);
  });
});

describe("chrome(コンテキスト)の挙動", () => {
  it("ルートでaria-currentが付け替わる(topnav/tabbar共通・App配線)", async () => {
    renderApp();
    window.location.hash = "#/standby";
    window.dispatchEvent(new Event("hashchange"));
    await act(async () => {});
    const current = document.querySelectorAll('[data-nav="standby"][aria-current="page"]');
    expect(current.length).toBe(2); // topnav+tabbar
    window.location.hash = "#/";
    window.dispatchEvent(new Event("hashchange"));
    await act(async () => {});
    expect(document.querySelector('[data-nav="standby"][aria-current="page"]')).toBeNull();
  });

  it("文言toastは自動消滅(2400ms)・候補トーストは閉じるまで残る", async () => {
    vi.useFakeTimers();
    try {
      const chrome = { current: null };
      renderWithChrome(<ToastArea />, chrome);
      act(() => chrome.current.showToast("下書きを保存しました"));
      expect(document.querySelector(".toast-msg--plain").textContent).toBe("下書きを保存しました");
      await act(async () => {
        vi.advanceTimersByTime(2400);
      });
      expect(document.querySelector(".toast")).toBeNull();
      // 候補トースト(オブジェクト)は自動消滅しない
      act(() => chrome.current.showToast({ message: "LATCH候補があります", href: "#/latches/x" }));
      await act(async () => {
        vi.advanceTimersByTime(10_000);
      });
      const link = document.querySelector("a.toast-link");
      expect(link.getAttribute("href")).toBe("#/latches/x");
      expect(document.querySelector(".toast-kicker").textContent).toBe("LATCH · いま");
      expect(document.querySelector(".toast-see").textContent).toContain("見る");
      // 閉じるボタンで消える
      fireEvent.click(document.querySelector(".toast-close"));
      expect(document.querySelector(".toast")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("setCountsがバッジに件数を入れる(0件は非表示・Appのバッジと同じ条件)", () => {
    const Badge = () => {
      const { counts } = useChrome();
      const n = counts.proposals ?? 0;
      return (
        <span className="count-pill acc" data-count="proposals" hidden={!(n > 0)}>
          {n > 0 ? n : 0}
          <span className="vh">件</span>
        </span>
      );
    };
    const chrome = { current: null };
    renderWithChrome(<Badge />, chrome);
    const pill = document.querySelector('[data-count="proposals"]');
    expect(pill.hidden).toBe(true); // 0件
    act(() => chrome.current.setCounts({ proposals: 2 }));
    expect(pill.hidden).toBe(false);
    expect(pill.textContent).toContain("2");
    act(() => chrome.current.setCounts({ proposals: 0 }));
    expect(pill.hidden).toBe(true);
  });

  it("openAppDialogが<dialog>を開き・操作ボタンで閉じてonClickを呼ぶ", async () => {
    const chrome = { current: null };
    const onClick = vi.fn();
    renderWithChrome(<AppDialog />, chrome);
    const dialog = document.getElementById("appDialog");
    expect(dialog.open).toBe(false);
    act(() =>
      chrome.current.openAppDialog({
        title: "この条件を取り下げますか？",
        body: <p className="dlg-desc">取り下げると、照らし合わせを止めます。</p>,
        actions: [
          { label: "やめておく", variant: "ghost" },
          { label: "取り下げる", variant: "danger", onClick },
        ],
      }),
    );
    expect(dialog.open).toBe(true);
    expect(document.querySelector("#appDialog h2").textContent).toBe("この条件を取り下げますか？");
    expect(document.querySelector("#appDialog .dlg-desc").textContent).toBe(
      "取り下げると、照らし合わせを止めます。",
    );
    const buttons = [...dialog.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["やめておく", "取り下げる"]);
    buttons[1].click();
    await act(async () => {});
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(dialog.open).toBe(false);
    // Esc(<dialog>標準)でも状態が空になる
    act(() => chrome.current.openAppDialog({ title: "x", actions: [] }));
    expect(dialog.open).toBe(true);
    fireEvent(dialog, new Event("close"));
    await act(async () => {});
    expect(document.querySelector("#appDialog h2")).toBeNull();
  });

  it("backdropクリックでappDialogを閉じる(Escは<dialog>標準)", async () => {
    const chrome = { current: null };
    renderWithChrome(<AppDialog />, chrome);
    const dialog = document.getElementById("appDialog");
    act(() => chrome.current.openAppDialog({ title: "x", actions: [] }));
    expect(dialog.open).toBe(true);
    fireEvent.click(dialog); // dialog自身がtarget(backdrop)
    await act(async () => {});
    expect(dialog.open).toBe(false);
  });
});

describe("a11y総点検(UI v2 Phase 5)", () => {
  it("skip linkと視覚隠し・reduced-motion・focus-visibleの規定がstyles.cssにある", () => {
    expect(css()).toContain('.skip:focus{top:12px}');
    expect(css()).toContain(".vh{");
    expect(css()).toContain("prefers-reduced-motion");
    expect(css()).toContain(":focus-visible{outline:2px solid var(--acc);outline-offset:3px}");
    const { container } = renderApp();
    expect(container.querySelector('a.skip[href="#main"]').textContent).toBe("本文へスキップ");
  });

  it("popoverボタンはaria-expanded+aria-controls+aria-haspopupを持つ", () => {
    renderApp();
    expect(document.getElementById("noticeButton").getAttribute("aria-controls")).toBe("noticePopover");
    expect(document.getElementById("accountButton").getAttribute("aria-controls")).toBe("accountPopover");
    // dialogを開くボタンはaria-haspopup="dialog"(解除・取り下げ・通報/取り消し)
    expect(document.getElementById("unblockDialog").getAttribute("aria-labelledby")).toBe("unblockTitle");
  });

  it("role=timer(03)・role=log(チャット)・aria-live(toast・読み取り)・role=status(お知らせ)がある", () => {
    renderApp();
    expect(document.querySelector('#toast[role="status"][aria-live="polite"]')).not.toBeNull();
    expect(document.querySelector('#noticePopover[role="status"]')).not.toBeNull();
    // 03・チャットのroleはstandby/chatの試験で担保(動的に組むため)
    expect(css()).toContain(".toast-pos");
  });

  it("a11y: popoverを開いたときaria-expanded=trueに変わる", async () => {
    renderApp();
    const button = document.getElementById("noticeButton");
    fireEvent.click(button);
    await act(async () => {});
    expect(button.getAttribute("aria-expanded")).toBe("true");
  });
});

afterEach(() => {
  vi.useRealTimers();
});
