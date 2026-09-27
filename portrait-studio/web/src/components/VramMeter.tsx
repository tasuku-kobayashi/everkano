import clsx from "clsx";
import { useVram } from "../api/queries";
import { formatMb, pct } from "../lib/format";
import { useUiStore } from "../store/ui";

/** Always-visible VRAM meter in the header (GET /api/system/vram every 5 s). */
export function VramMeter() {
  const { data, isError } = useVram();
  const warnPercent = useUiStore((s) => s.vramWarnPercent);
  if (isError || !data) {
    return (
      <div className="text-xs text-rose-300" data-testid="vram-meter" data-state="offline">
        VRAM: ComfyUI 未接続
      </div>
    );
  }
  const used = pct(data.used_mb, data.total_mb);
  const warn = used >= warnPercent;
  return (
    <div className="w-56 text-[11px]" data-testid="vram-meter" data-state="online" title={data.gpu ?? ""}>
      <div className="flex justify-between text-slate-300">
        <span>VRAM {formatMb(data.used_mb)} / {formatMb(data.total_mb)}</span>
        <span className={clsx("font-mono", warn && "text-rose-300")}>{used}%</span>
      </div>
      <div className="mt-0.5 h-2 rounded bg-ink-700">
        <div className={clsx("h-2 rounded", warn ? "bg-rose-500" : used > 60 ? "bg-amber-400" : "bg-emerald-500")} style={{ width: `${used}%` }} />
      </div>
      <div className="text-slate-500">空き {formatMb(data.free_mb)}</div>
    </div>
  );
}
