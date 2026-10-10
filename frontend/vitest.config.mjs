import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// vitest設定(worktree内 .herdr 配下でvite.config.mjsのtestキーが
// 自動検出されないため分離。機能は計画書の記載と等価)。
// UI v2 Phase 6(React載せ替え)からplugin-reactとRTLを併用する
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.{js,jsx}"],
  },
});
