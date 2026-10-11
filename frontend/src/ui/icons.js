// SVGアイコン一式(prototype/v2 01〜05のSVGをそのまま写したもの。
// 消費側はsrc/ui/Icon.jsx(React版)。Phase 1時点のicon/logoSvg/iconHtml
// (DOM組立)はPhase 6のReact載せ替えで未使用になり、Phase 7の清掃で削除した。
// 独自のアイコンは足さない(UI v2 Phase 1の規律)。

// 各アイコン: { viewBox, sw(stroke-width), body } — bodyはプロトタイプの
// 子要素マークアップをそのまま記載する(currentColorで描画)。
// Phase 6(React載せ替え)からはsrc/ui/Icon.jsxがこの表を消費する
export const ICONS = {
  // 01 ヘッダーベル・02 公開範囲と通知(stroke 1.6)
  bell: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"></path><path d="M10 20a2 2 0 0 0 4 0"></path>',
  },
  // 01 鍵(stroke 1.6)
  lock: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="M8 11V8a4 4 0 0 1 8 0v3"></path>',
  },
  // 01 CTA・「すべて見る」矢印(stroke 2)
  arrowRight: {
    viewBox: "0 0 24 24",
    sw: 2,
    body: '<path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path>',
  },
  // 02 時刻アイコン(stroke 1.6)
  clock: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<circle cx="12" cy="12" r="8"></circle><path d="M12 8v4l3 2"></path>',
  },
  // 02 場所(stroke 1.6)
  pin: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<path d="M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11z"></path><circle cx="12" cy="10" r="2.2"></circle>',
  },
  // 02 人数(stroke 1.6)
  people: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<circle cx="9" cy="9" r="3"></circle><path d="M3.5 19a5.5 5.5 0 0 1 11 0"></path><circle cx="17" cy="10" r="2.4"></circle><path d="M15.5 14.6A4.5 4.5 0 0 1 21 19"></path>',
  },
  // 02 予算(stroke 1.6)
  yen: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<path d="M7 5l5 7 5-7"></path><path d="M12 12v7"></path><path d="M8 13h8"></path><path d="M8 16h8"></path>',
  },
  // 01・02 目的(グラス・stroke 1.6)
  glass: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<path d="M6 4h12l-1.5 7a4.5 4.5 0 0 1-9 0z"></path><path d="M12 15.5V20"></path><path d="M8.5 20h7"></path>',
  },
  // 01 転職カテゴリ(stroke 1.6)
  briefcase: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<rect x="3.5" y="7" width="17" height="12" rx="2"></rect><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"></path><path d="M3.5 12.5h17"></path>',
  },
  // 01 売買カテゴリ(stroke 1.6)
  car: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<path d="M5 12l2-5h10l2 5"></path><rect x="3" y="12" width="18" height="5" rx="1.5"></rect><path d="M6.5 17v2M17.5 17v2"></path>',
  },
  // 01 読み取れない案内(stroke 1.8)
  info: {
    viewBox: "0 0 24 24",
    sw: 1.8,
    body: '<circle cx="12" cy="12" r="8.5"></circle><path d="M12 11v5"></path><path d="M12 8h.01"></path>',
  },
  // 01 未読み取りチップの補完(stroke 2)— 02 足せる条件(オフ)
  plus: {
    viewBox: "0 0 24 24",
    sw: 2,
    body: '<path d="M12 5v14M5 12h14"></path>',
  },
  // 02・04 チェック(stroke 2.4)
  check: {
    viewBox: "0 0 24 24",
    sw: 2.4,
    body: '<path d="M5 12.5l4.5 4.5L19 7"></path>',
  },
  // 02 行編集(stroke 1.7)
  pencil: {
    viewBox: "0 0 24 24",
    sw: 1.7,
    body: '<path d="M4 20h4L19 9l-4-4L4 16z"></path><path d="M13.5 6.5l4 4"></path>',
  },
  // 03 トースト閉じる(stroke 1.8)
  close: {
    viewBox: "0 0 24 24",
    sw: 1.8,
    body: '<path d="M6 6l12 12M18 6L6 18"></path>',
  },
  // 04 目隠しアバター(stroke 1.6)
  eyeOff: {
    viewBox: "0 0 24 24",
    sw: 1.6,
    body: '<path d="M3 3l18 18"></path><path d="M10.6 6.1A9.6 9.6 0 0 1 12 6c5 0 8.5 4.5 9 6-.3.8-1.2 2.3-2.7 3.6M6.4 7.6C4.5 8.9 3.3 10.8 3 12c.5 1.5 4 6 9 6 1.4 0 2.6-.3 3.7-.8"></path><path d="M9.9 10a3 3 0 0 0 4.1 4.1"></path>',
  },
  // 05 detailsのシェブロン(stroke 1.8)
  chevronDown: {
    viewBox: "0 0 24 24",
    sw: 1.8,
    body: '<path d="M6 9l6 6 6-6"></path>',
  },
  // 05 カレンダー(stroke 1.7)
  calendar: {
    viewBox: "0 0 24 24",
    sw: 1.7,
    body: '<rect x="4" y="5" width="16" height="15" rx="2"></rect><path d="M4 10h16M9 3v4M15 3v4"></path>',
  },
  // 05 安全のために(stroke 1.7)
  shield: {
    viewBox: "0 0 24 24",
    sw: 1.7,
    body: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"></path>',
  },
  // 05 通報・ブロックの旗(stroke 1.7)
  flag: {
    viewBox: "0 0 24 24",
    sw: 1.7,
    body: '<path d="M5 21V4h11l-1.5 4L16 12H5"></path>',
  },
  // 05 送信(紙飛行機・stroke 2)
  send: {
    viewBox: "0 0 24 24",
    sw: 2,
    body: '<path d="M4 12l16-8-6 16-2.5-6.5z"></path><path d="M11.5 13.5L20 4"></path>',
  },
  // 01 CTA内スピナー(stroke 2.4・.spinnerで回転)
  spinner: {
    viewBox: "0 0 24 24",
    sw: 2.4,
    body: '<path d="M12 3a9 9 0 1 1-9 9"></path>',
  },
  // 04 「参加する」ボタン内のLATCH弧(stroke 4・currentColor)
  latchMark: {
    viewBox: "0 0 40 40",
    sw: 4,
    body: '<path d="M20.5 5 A15 15 0 0 0 20.5 35"></path><path d="M20.5 5 A15 15 0 0 1 20.5 35"></path>',
  },
};
