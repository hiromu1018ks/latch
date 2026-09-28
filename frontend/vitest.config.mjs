import { defineConfig } from "vitest/config";

// vitest設定(worktree内 .herdr 配下でvite.config.mjsのtestキーが
// 自動検出されないため分離。機能は計画書の記載と等価)
export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.js"],
  },
});
