import clsx from "clsx";
import type { AnalyzeItem } from "../api/client";
import { BlurImage } from "./BlurImage";
import { Meter, QualityBadge, WarningChips } from "./Badges";

interface Props {
  item: AnalyzeItem;
  selected: boolean;
  primary: boolean;
  recommended: boolean;
  clusterIndex: number | null;
  thresholds: Record<string, number>;
  onToggle: () => void;
  onPrimary: () => void;
}

/** Wizard step 2 card: visible quality metrics, warnings, cluster tag, recommendation highlight. */
export function QualityCard({ item, selected, primary, recommended, clusterIndex, thresholds, onToggle, onPrimary }: Props) {
  const q = item.quality;
  const usable = item.usable && !!q;
  return (
    <div
      className={clsx(
        "card flex flex-col gap-2 p-2 transition-colors",
        recommended && "border-emerald-500 ring-1 ring-emerald-500",
        selected && !recommended && "border-accent",
        !usable && "opacity-60",
      )}
      data-testid="quality-card"
      data-usable={usable ? "true" : "false"}
      data-recommended={recommended ? "true" : "false"}
    >
      <div className="relative">
        <BlurImage id={item.image_id} src={item.thumb_url} alt={item.filename} className="aspect-[3/4] w-full rounded" />
        {recommended && <span className="absolute left-1 top-1 rounded bg-emerald-600 px-1.5 py-0.5 text-[11px] font-semibold text-white">推奨</span>}
        {clusterIndex !== null && <span className="absolute right-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[11px] text-white">グループ {clusterIndex + 1}</span>}
      </div>
      <div className="flex items-center justify-between">
        <QualityBadge quality={q ?? null} />
        <span className="text-[11px] text-slate-400">顔 {item.face_count} 件</span>
      </div>
      {q && (
        <div className="space-y-1">
          <Meter label="顔サイズ比率" value={q.face_ratio} max={0.3} format={(v) => v.toFixed(3)} warn={q.face_ratio < (thresholds.face_ratio_min ?? 0.08)} />
          <Meter label="正面度 (|yaw|)" value={Math.abs(q.yaw)} max={45} format={(v) => `${v.toFixed(0)}°`} warn={Math.abs(q.yaw) > (thresholds.yaw_max_deg ?? 15)} />
          <Meter label="シャープネス" value={q.sharpness} max={400} format={(v) => v.toFixed(0)} warn={q.sharpness < (thresholds.sharpness_min ?? 60)} />
          <Meter label="検出信頼度" value={q.det_score} max={1} format={(v) => v.toFixed(2)} warn={q.det_score < (thresholds.det_score_min ?? 0.6)} />
        </div>
      )}
      <WarningChips warnings={item.warnings} />
      <div className="mt-auto flex items-center justify-between gap-2">
        <label className={clsx("flex items-center gap-1 text-xs", !usable && "cursor-not-allowed")}>
          <input type="checkbox" checked={selected} disabled={!usable} onChange={onToggle} aria-label={`${item.filename} を参照顔に使う`} />
          使う
        </label>
        <label className={clsx("flex items-center gap-1 text-xs", (!usable || !selected) && "opacity-50")}>
          <input type="radio" name="primary" checked={primary} disabled={!usable || !selected} onChange={onPrimary} aria-label={`${item.filename} を primary にする`} />
          primary
        </label>
      </div>
    </div>
  );
}
