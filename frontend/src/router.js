// hashルーター(M3 ws-7 design §2.1案A)。`#/`=ホーム・`#/latches/{uuid}`=
// LATCH詳細・未知のhashはホームへ戻す。ルーターはURL解析と画面sectionの
// 切替のみを担い、各画面の初期化・データ取得はonRoute側(単体試験可能な境界)。
// uuidはbackendの発行形式(uuid4・バージョン桁4+バリアント桁89ab)に合わせる。
const LATCH_HASH = /^#\/latches\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$/;

export const parseHash = (hash) => {
  const match = LATCH_HASH.exec(hash ?? "");
  if (match) return { name: "latch", id: match[1] };
  return { name: "home", id: null };
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
