// 開発用トークンパネル(design §2.3・トークン未設定時のみ表示・UI v2 Phase 6で
// React化)。旧main.jsのDOM配線をコンポーネントへ移したもので、挙動は同じ:
// 接続でPOST /v1/auth/token → session.save → パネルを閉じて未同意なら誘導。
// 401は再発行を促す文言・それ以外はAPI起動確認の文言。
import { useState } from "react";
import { exchangeIdpToken } from "../api/session.js";

export const TokenPanel = ({ session, visible, onHide, onConnected }) => {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!visible) return null;

  const connect = async () => {
    const idpToken = document.querySelector("#idpToken").value.trim();
    if (!idpToken) return;
    setBusy(true);
    try {
      const tokens = await exchangeIdpToken((...args) => fetch(...args), {
        provider: "google",
        idpToken,
      });
      session.save(tokens);
      document.querySelector("#idpToken").value = "";
      onHide();
      onConnected?.();
    } catch (err) {
      setError(
        err?.status === 401
          ? "トークンが無効です。再発行して貼り直してください。"
          : "接続できませんでした。APIの起動を確認してください。",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="token-panel" id="tokenPanel" aria-labelledby="token-title">
      <p className="section-kicker"><span></span>DEV AUTH</p>
      <h2 id="token-title">開発用トークンパネル</h2>
      <p className="token-note">APIを利用するには認証が必要です(05 §5)。backendの内部CLIで発行したIdPトークンを貼り付けてください。手順は frontend/README.md を参照。</p>
      <textarea id="idpToken" rows="3" aria-label="IdPトークン" placeholder="eyJhbGciOiJSUzI1NiIs…"></textarea>
      <button className="token-connect" id="tokenConnect" type="button" onClick={connect} disabled={busy}>接続する</button>
      <p className="token-error" id="tokenError" role="status" hidden={!error}>{error}</p>
    </section>
  );
};
