// トークン管理(design §2.3・告白3承認済み)。
// access: メモリ+sessionStorage(1時間・タブ閉で消える)
// refresh: localStorage(30日回転式・再訪時の継続利用)
export const ACCESS_KEY = "latch-access";
export const REFRESH_KEY = "latch-refresh";

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message ?? code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const parseEnvelope = async (res) => {
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, data?.error?.code ?? `HTTP_${res.status}`, data?.error?.message);
  }
  return data;
};

export const createSession = ({ storage } = {}) => {
  const sessionStore = storage?.session ?? sessionStorage;
  const localStore = storage?.local ?? localStorage;
  const memory = { accessToken: null };

  return {
    current: () => ({
      accessToken: memory.accessToken ?? sessionStore.getItem(ACCESS_KEY),
      refreshToken: localStore.getItem(REFRESH_KEY),
    }),
    save: ({ access_token, refresh_token }) => {
      if (access_token) {
        memory.accessToken = access_token;
        sessionStore.setItem(ACCESS_KEY, access_token);
      }
      if (refresh_token) localStore.setItem(REFRESH_KEY, refresh_token);
    },
    clear: () => {
      memory.accessToken = null;
      sessionStore.removeItem(ACCESS_KEY);
      localStore.removeItem(REFRESH_KEY);
    },
    hasTokens: () =>
      Boolean(memory.accessToken ?? sessionStore.getItem(ACCESS_KEY) ?? localStore.getItem(REFRESH_KEY)),
  };
};

// 開発用トークンパネル→POST /v1/auth/token(本番IdPフローと共通の交換経路・design §2.3)
export const exchangeIdpToken = (fetchImpl, { provider, idpToken }) =>
  fetchImpl("/v1/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, idp_token: idpToken }),
  }).then(parseEnvelope);
