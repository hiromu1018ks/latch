// 画面横断状態(M3 ws-7 design §3)。自分のuser_idはGET /v1/users/meで
// セッション確立後に1回だけ取得しメモリ保持する(再ログインで再取得)。

export const createAppState = ({ client }) => {
  let me = null;
  return {
    get me() {
      return me;
    },
    async ensureMe() {
      if (me) return me;
      const data = await client.call("GET", "/v1/users/me");
      me = { id: data.id, display_name: data.display_name, profile: data.profile };
      return me;
    },
    reset() {
      me = null;
    },
  };
};
