// APIクライアント(design §2.3)。全API通信はこのcall()を通る:
// - Authorizationヘッダー付与
// - 共通envelope {error:{code,message,details}} の解釈(ApiErrorはcodeで分岐・確定値13)
// - 401 UNAUTHENTICATED → POST /v1/auth/refresh を1回試み、成功なら元リクエストを再送
//   失敗ならセッション破棄してonSessionExpiredへ(トークンパネルへ戻す)
import { ApiError } from "./session.js";

const doFetch = async (fetchImpl, method, path, { body, signal, token } = {}) => {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetchImpl(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
};

const refreshOnce = async (session, fetchImpl) => {
  const refreshToken = session.current().refreshToken;
  if (!refreshToken) return false;
  try {
    const res = await doFetch(fetchImpl, "POST", "/v1/auth/refresh", {
      body: { refresh_token: refreshToken },
    });
    if (!res.ok) return false;
    const data = await res.json();
    session.save({ access_token: data.access_token, refresh_token: data.refresh_token });
    return true;
  } catch {
    return false;
  }
};

export const createClient = ({ session, fetchImpl, onSessionExpired = () => {} }) => {
  const fetcher = fetchImpl ?? ((...args) => fetch(...args));
  // 進行中のrefreshは1つに束ねる。回転式refreshは同一トークンの再利用を検知すると
  // 族ごと失効させるため、並行401(起動時の複数呼び出し等)でそれぞれrefreshを
  // 投げると正規ユーザーがログアウトされる(findings#1)。
  let refreshing = null;
  const refreshShared = () => {
    refreshing ??= refreshOnce(session, fetcher).finally(() => {
      refreshing = null;
    });
    return refreshing;
  };
  return {
    call: async (method, path, { body, signal } = {}) => {
      let res = await doFetch(fetcher, method, path, {
        body,
        signal,
        token: session.current().accessToken,
      });
      if (res.status === 401 && session.current().refreshToken) {
        const refreshed = await refreshShared();
        if (refreshed) {
          res = await doFetch(fetcher, method, path, {
            body,
            signal,
            token: session.current().accessToken,
          });
        } else {
          session.clear();
          onSessionExpired();
        }
      } else if (res.status === 401) {
        session.clear();
        onSessionExpired();
      }
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        throw new ApiError(
          res.status,
          data?.error?.code ?? `HTTP_${res.status}`,
          data?.error?.message,
        );
      }
      return data;
    },
  };
};
