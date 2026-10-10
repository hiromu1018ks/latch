// RTL用ヘルパー(UI v2 Phase 6)。Appをスタブsessionで描画する。
// RTLの自動cleanupはvitestのglobals無しでは走らないため、このモジュールの
// importだけでafterEach(cleanup)が効くようにする。
import { afterEach, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ChromeProvider, useChrome } from "../src/ui/chrome.jsx";
import { App } from "../src/App.jsx";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

export const stubSession = (overrides = {}) => ({
  hasTokens: () => false,
  current: () => ({ accessToken: null, refreshToken: null }),
  save: () => {},
  clear: () => {},
  ...overrides,
});

// 既定のスタブclient: 一覧系APIは空応答を返す(通信なしで決定的に描画する)。
// callの履歴はclient.call.mockで確認できる
export const stubClient = () => {
  const fn = vi.fn(async () => ({ items: [], next_cursor: null }));
  return fn;
};

export const renderApp = ({ session, client } = {}) => {
  const utils = render(
    <App
      session={session ?? stubSession()}
      client={client ?? stubClient()}
    />,
  );
  return utils;
};

// chrome API単体の描画試験用。probeの子がuseChrome()の戻り値をcaptureする
export const renderWithChrome = (ui, capture = { current: null }) => {
  const Probe = () => {
    capture.current = useChrome();
    return null;
  };
  const utils = render(
    <ChromeProvider>
      {ui}
      <Probe />
    </ChromeProvider>,
  );
  return { ...utils, chrome: capture };
};
