// hashルーター(M3 ws-7 design §2.1案A・ws-8 §2.4)。`#/`=ホーム(01預ける)・
// `#/confirm[?intent={uuid}]`=読み解き確認(02・新規=照会なし/条件を直す=Intent編集)・
// `#/standby[?deposited=1]`=待機中(03・着地状態はクエリで表現)・
// `#/latches/{uuid}`=LATCH詳細(04/05)・`#/settings`=設定・未知のhashは
// ホームへ戻す。ルーターはURL解析と画面sectionの切替のみを担い、各画面の初期化・
// データ取得はonRoute側(単体試験可能な境界)。
// uuidはbackendの発行形式(uuid4・バージョン桁4+バリアント桁89ab)に合わせる。
const LATCH_HASH = /^#\/latches\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$/;
const CONFIRM_HASH = /^#\/confirm(?:\?intent=([0-9a-fA-F-]+))?$/;
const STANDBY_HASH = /^#\/standby(?:\?(.*))?$/;

// クエリ部(key=value&…)をオブジェクトへ。値はdecodeURIComponentする
const parseQuery = (raw) => {
  const query = {};
  if (!raw) return query;
  for (const pair of raw.split("&")) {
    const eq = pair.indexOf("=");
    if (eq < 1) continue;
    try {
      query[pair.slice(0, eq)] = decodeURIComponent(pair.slice(eq + 1));
    } catch {
      query[pair.slice(0, eq)] = pair.slice(eq + 1);
    }
  }
  return query;
};

export const parseHash = (hash) => {
  if (hash === "#/settings") return { name: "settings", id: null, query: {} };
  if (hash === "#/onboarding") {
    return { name: "onboarding", id: null, query: {} }; // M4 ws-5・厳密一致
  }
  const confirmMatch = CONFIRM_HASH.exec(hash ?? "");
  if (confirmMatch) {
    // UI v2 02(id=編集対象のIntent・03の「条件を直す」から)。不正なuuid値は
    // この先のGET /v1/intents/{id}の404で扱う
    return { name: "confirm", id: confirmMatch[1] ?? null, query: {} };
  }
  const standbyMatch = STANDBY_HASH.exec(hash ?? "");
  if (standbyMatch) {
    // UI v2 03(deposited=1は02からの着地状態)
    return { name: "standby", id: null, query: parseQuery(standbyMatch[1]) };
  }
  const match = LATCH_HASH.exec(hash ?? "");
  if (match) return { name: "latch", id: match[1], query: {} };
  return { name: "home", id: null, query: {} };
};

export const createRouter = ({ screens, onRoute }) => {
  const apply = () => {
    const route = parseHash(window.location.hash);
    for (const [name, el] of Object.entries(screens)) {
      el.hidden = route.name !== name;
    }
    onRoute?.(route);
  };
  return {
    start() {
      window.addEventListener("hashchange", apply);
      apply();
    },
    stop() {
      window.removeEventListener("hashchange", apply);
    },
  };
};
