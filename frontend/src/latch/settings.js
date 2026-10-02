// 設定画面(M3 ws-8 design §2.4)。2セクション(通知許可・ブロック管理)の
// マウントと#/settings表示。規定のない要素(プロフィール編集・ログアウト・
// テーマ等)は置かない(design §1.4)。
export const createSettings = ({ permissionControl, blocks }) => ({
  show() {
    permissionControl.render();
    blocks.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
  },
});
