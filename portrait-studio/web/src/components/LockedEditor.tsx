import type { FaceMethod, LockedPatch } from "../api/client";
import { METHOD_LABEL } from "../lib/format";

interface Props {
  value: LockedPatch;
  onChange: (value: LockedPatch) => void;
  checkpoints: string[];
  faceMethods: string[];
  showResolution?: boolean;
}

/** Editable locked params (wizard step 4 defaults / new version). face_detailer denoise is capped below 0.5. */
export function LockedEditor({ value, onChange, checkpoints, faceMethods, showResolution = true }: Props) {
  const set = (patch: Partial<LockedPatch>) => onChange({ ...value, ...patch });
  const methods = faceMethods.length ? faceMethods : ["pulid", "faceid", "instantid"];
  return (
    <div className="grid gap-3 text-sm md:grid-cols-2" data-testid="locked-editor">
      <label className="block">
        <span className="label">checkpoint</span>
        {checkpoints.length ? (
          <select className="input" value={value.checkpoint ?? ""} onChange={(e) => set({ checkpoint: e.target.value || null })}>
            <option value="">（既定: 最初のチェックポイント）</option>
            {checkpoints.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : (
          <input className="input" value={value.checkpoint ?? ""} onChange={(e) => set({ checkpoint: e.target.value || null })} placeholder="model.safetensors" />
        )}
      </label>
      <label className="block">
        <span className="label">face_method</span>
        <select className="input" value={value.face_method ?? "pulid"} onChange={(e) => set({ face_method: e.target.value as FaceMethod })}>
          {methods.map((m) => (
            <option key={m} value={m}>
              {METHOD_LABEL[m] ?? m}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="label">face_weight ({value.face_weight ?? 0.8})</span>
        <input type="range" min={0} max={1.5} step={0.05} value={value.face_weight ?? 0.8} onChange={(e) => set({ face_weight: Number(e.target.value) })} className="w-full" />
      </label>
      <label className="block">
        <span className="label">face_detailer denoise ({value.face_detailer_denoise ?? 0.35}) — 0.5 以上は不可</span>
        <input type="range" min={0.2} max={0.49} step={0.01} value={value.face_detailer_denoise ?? 0.35} onChange={(e) => set({ face_detailer_denoise: Number(e.target.value) })} className="w-full" />
      </label>
      {showResolution && (
        <>
          <label className="block">
            <span className="label">既定の幅</span>
            <input className="input" type="number" step={8} min={512} max={2048} value={value.default_width ?? 832} onChange={(e) => set({ default_width: Number(e.target.value) })} />
          </label>
          <label className="block">
            <span className="label">既定の高さ</span>
            <input className="input" type="number" step={8} min={512} max={2048} value={value.default_height ?? 1216} onChange={(e) => set({ default_height: Number(e.target.value) })} />
          </label>
        </>
      )}
      <label className="block">
        <span className="label">hires 上限 (×{value.hires_max ?? 1.3})</span>
        <input type="range" min={1} max={2} step={0.05} value={value.hires_max ?? 1.3} onChange={(e) => set({ hires_max: Number(e.target.value) })} className="w-full" />
      </label>
      <label className="block md:col-span-2">
        <span className="label">固定プレフィックス（人種・外見はここに置く。毎回のプロンプトには書かない）</span>
        <textarea className="input font-mono text-xs" rows={3} value={value.prefix_prompt ?? ""} onChange={(e) => set({ prefix_prompt: e.target.value })} />
      </label>
      <label className="block md:col-span-2">
        <span className="label">ネガティブプロンプト</span>
        <textarea className="input font-mono text-xs" rows={3} value={value.negative_prompt ?? ""} onChange={(e) => set({ negative_prompt: e.target.value })} />
      </label>
    </div>
  );
}
