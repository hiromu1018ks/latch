// セッション開始ビーコン(O-07・04 §2・05 §19)。DAU・再来訪の素材となる
// セッション開始イベント(user_idと時刻のみ・Intentの文言を含まない)を
// POST /v1/sessionsへ送る。fire-and-forget(204・失敗は握る=画面動作の妨げに
// しない)。起動時とトークン接続直後(=新しいセッションの開始)の2契機。
export const reportSessionStart = async ({ client }) => {
  try {
    await client.call("POST", "/v1/sessions", {});
    return true;
  } catch {
    return false; // 401(未登録ユーザー)等も握る(計測の失敗で画面を壊さない)
  }
};
