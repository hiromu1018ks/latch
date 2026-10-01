import { afterEach, describe, expect, it, vi } from "vitest";
import { createRouter, parseHash } from "../src/router.js";

const UUID = "11111111-1111-4111-8111-111111111111";

const mountScreens = () => ({
  home: document.createElement("div"),
  latch: document.createElement("div"),
});

afterEach(() => {
  window.location.hash = "";
});

describe("parseHash(design §2.1)", () => {
  it("#/ と空と未知のhashはホームへ", () => {
    expect(parseHash("#/")).toEqual({ name: "home", id: null });
    expect(parseHash("")).toEqual({ name: "home", id: null });
    expect(parseHash("#/unknown/path")).toEqual({ name: "home", id: null });
    expect(parseHash("#/latches/")).toEqual({ name: "home", id: null });
  });

  it("#/latches/{uuid} を解析する", () => {
    expect(parseHash(`#/latches/${UUID}`)).toEqual({
      name: "latch",
      id: UUID,
    });
  });

  it("uuid形式でなければホームへ戻す", () => {
    expect(parseHash("#/latches/not-a-uuid").name).toBe("home");
    expect(parseHash("#/latches/11111111-1111-1111-1111-111111111111").name)
      .toBe("home"); // 4が無くuuid形式として不正
  });
});

describe("createRouter(hashchangeで画面section切替)", () => {
  it("startは現在hashで解決しsectionのhiddenを切替+onRouteを呼ぶ", () => {
    const screens = mountScreens();
    screens.home.hidden = true; // 初期状態を裏返しておく
    screens.latch.hidden = false;
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    expect(screens.home.hidden).toBe(false);
    expect(screens.latch.hidden).toBe(true);
    expect(onRoute).toHaveBeenCalledWith({ name: "home", id: null });
    router.stop();
  });

  it("hashchangeでlatch画面へ切替(戻るも可)", () => {
    const screens = mountScreens();
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    window.location.hash = `#/latches/${UUID}`;
    window.dispatchEvent(new Event("hashchange"));
    expect(screens.home.hidden).toBe(true);
    expect(screens.latch.hidden).toBe(false);
    expect(onRoute).toHaveBeenLastCalledWith({ name: "latch", id: UUID });
    window.location.hash = "#/";
    window.dispatchEvent(new Event("hashchange"));
    expect(screens.home.hidden).toBe(false);
    router.stop();
  });

  it("stopでhashchangeをlistenしない", () => {
    const screens = mountScreens();
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    router.stop();
    onRoute.mockClear();
    window.location.hash = `#/latches/${UUID}`;
    window.dispatchEvent(new Event("hashchange"));
    expect(onRoute).not.toHaveBeenCalled();
  });
});
