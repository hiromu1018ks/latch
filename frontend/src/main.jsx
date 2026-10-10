// エントリポイント(UI v2 Phase 6)。Reactのマウントのみを担い、シェルと画面の
// 配線はApp(コンテキスト+hooks・移行期ブリッジ)へ委譲する。セッションは
// ここで1回生成してAppへ注入する(試験からはスタブsessionを注入できる)。
// 意図文言をログに出さない(01 §21)。
import { createRoot } from "react-dom/client";
import { createSession } from "./api/session.js";
import { App } from "./App.jsx";

const session = createSession();

createRoot(document.getElementById("root")).render(<App session={session} />);
