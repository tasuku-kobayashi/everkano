import type { LockedParams } from "../api/client";
import { METHOD_LABEL } from "../lib/format";

/** Read-only view of the character's locked (identity) parameters. */
export function LockedPanel({ locked, version }: { locked: LockedParams; version?: number }) {
  const rows: Array<[string, string]> = [
    ["checkpoint", locked.checkpoint],
    ["face_method", METHOD_LABEL[locked.face_method ?? "pulid"] ?? String(locked.face_method)],
    ["face_weight", String(locked.face_weight ?? 0.8)],
    ["解像度", `${locked.default_width}×${locked.default_height}`],
    ["face_detailer denoise", String(locked.face_detailer_denoise)],
    ["hires 上限", `×${locked.hires_max}`],
    ["LoRA", locked.lora ? `${locked.lora} (${locked.lora_strength})` : "なし"],
  ];
  return (
    <div className="text-xs" data-testid="locked-panel">
      <div className="mb-1 flex items-center justify-between text-slate-400">
        <span>固定設定（読み取り専用）</span>
        <span className="badge bg-ink-700 text-slate-200">🔒 {version ? `v${version}` : ""}</span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="truncate font-mono text-slate-200" title={v}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
      <details className="mt-1">
        <summary className="cursor-pointer text-slate-500">▸ プレフィックス / ネガティブ</summary>
        <p className="mt-1 whitespace-pre-wrap break-words rounded bg-ink-800 p-1.5 font-mono text-[10px] text-slate-300">{locked.prefix_prompt}</p>
        <p className="mt-1 whitespace-pre-wrap break-words rounded bg-ink-800 p-1.5 font-mono text-[10px] text-slate-400">{locked.negative_prompt}</p>
      </details>
    </div>
  );
}
