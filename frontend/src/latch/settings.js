// 設定画面(M3 ws-8 design §2.4・M4 ws-5)。3セクション(通知許可・ブロック管理・
// LLM送信の同意)のマウントと#/settings表示。規定のない要素(プロフィール編集・
// ログアウト・テーマ等)は置かない(design §1.4)。通知許可(D-18)とLLM送信同意
// (D-14)は根拠も効果も別物のため区分表示を維持する。
export const createSettings = ({ permissionControl, blocks, consent }) => ({
  show() {
    permissionControl.render();
    blocks.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
    consent.load().catch(() => {}); // 第3セクション(LLM送信の同意・M4 ws-5)
  },
});
