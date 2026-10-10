// SVGアイコンのReact版(UI v2 Phase 6)。pathデータ・stroke幅はsrc/ui/icons.jsの
// 表(プロトタイプのSVGを写したもの)をそのまま使い、出力されるDOMも旧icon()と
// 同形(width/height/viewBox/fill/stroke系/data-icon/aria-hidden)に揃える。
// 独自のアイコンは足さない(Phase 1の規律)。
import { ICONS } from "./icons.js";

export const Icon = ({ name, size = 18, className }) => {
  const def = ICONS[name];
  if (!def) throw new Error(`unknown icon: ${name}`);
  return (
    <svg
      width={size}
      height={size}
      viewBox={def.viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth={def.sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      data-icon={name}
      className={className}
      dangerouslySetInnerHTML={{ __html: def.body }}
    />
  );
};

// ロゴ(ヘッダー・トースト用)。左右2弧の色はプロトタイプ固定
// (左弧=t1・右弧=acc)のためcurrentColorではなく個別指定。
export const Logo = ({ size = 30 }) => (
  <svg width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden="true"
    dangerouslySetInnerHTML={{
      __html:
        '<path d="M19 5 A15 15 0 0 0 19 35" style="stroke: var(--t1)" stroke-width="3.4" stroke-linecap="round"></path>' +
        '<path d="M22 5 A15 15 0 0 1 22 35" style="stroke: var(--acc)" stroke-width="3.4" stroke-linecap="round"></path>',
    }}
  />
);
