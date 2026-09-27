import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage, unwrap, type AnalyzeResponse } from "../api/client";
import { useCharacter, useVersions } from "../api/queries";
import { QualityBadge } from "../components/Badges";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Dropzone } from "../components/Dropzone";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { formatDate, METHOD_LABEL } from "../lib/format";

/** Versions: reference replacement always creates a NEW version (old versions stay, rollback is one click). */
export function VersionsDialog({ characterId, onClose }: { characterId: string; onClose: () => void }) {
  const { data: character } = useCharacter(characterId);
  const { data: versions, refetch } = useVersions(characterId);
  const qc = useQueryClient();
  const toast = useToast();
  const [replacing, setReplacing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [note, setNote] = useState("正面の良い1枚に差し替え");
  const [confirm, setConfirm] = useState(false);

  const invalidate = async () => {
    await refetch();
    await qc.invalidateQueries({ queryKey: ["character", characterId] });
    await qc.invalidateQueries({ queryKey: ["characters"] });
  };

  const upload = async (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    try {
      const r = unwrap(await api.POST("/api/characters/analyze", { body: form as unknown as { files?: string[] } }));
      setAnalysis(r);
      const rec = r.recommended_index !== null ? r.items[r.recommended_index] : r.items.find((i) => i.usable);
      setChosen(rec?.image_id ?? null);
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  const createVersion = async () => {
    if (!chosen) return;
    try {
      const v = unwrap(await api.POST("/api/characters/{character_id}/versions", { params: { path: { character_id: characterId } }, body: { reference_image_ids: [chosen], primary_image_id: chosen, note } }));
      toast.success(`v${v.version} を作成し current にしました`);
      setReplacing(false);
      setAnalysis(null);
      setConfirm(false);
      await invalidate();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  const rollback = async (version: number) => {
    try {
      unwrap(await api.POST("/api/characters/{character_id}/rollback/{version}", { params: { path: { character_id: characterId, version } } }));
      toast.success(`v${version} を current に戻しました`);
      await invalidate();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  return (
    <Modal open onClose={onClose} title={`版の管理 — ${character?.name ?? ""}`} wide testId="versions-dialog">
      <p className="mb-3 text-xs text-slate-400">
        参照顔の差し替えは<strong>新しい版として分岐</strong>します（上書きしません）。各生成物はどの版で作られたかを持つので、一貫性がいつ壊れたかを特定できます。
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {versions?.items.map((v) => {
          const current = v.version === versions.current_version;
          return (
            <div key={v.version} className={current ? "card border-accent p-3" : "card p-3"} data-testid="version-card">
              <div className="flex items-center justify-between">
                <div className="font-medium">
                  v{v.version} {current && <span className="badge bg-accent text-white">current</span>}
                </div>
                <span className="text-[11px] text-slate-500">{formatDate(v.created_at)}</span>
              </div>
              <div className="mt-2 flex gap-2">
                {v.references.map((r) => (
                  <div key={r.id} className="w-20">
                    <img src={r.file_url} alt={`v${v.version} 参照顔`} className="aspect-[3/4] w-20 rounded object-cover" />
                    <div className="mt-1">
                      <QualityBadge quality={r.quality} />
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 text-xs text-slate-400">{v.note || "—"}</div>
              <div className="mt-1 text-[11px] font-mono text-slate-500">
                {METHOD_LABEL[v.locked.face_method ?? "pulid"]} · weight {v.locked.face_weight} · {v.locked.checkpoint}
              </div>
              {!current && (
                <button className="btn-secondary mt-2" onClick={() => rollback(v.version)}>
                  この版を current に戻す
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-4 border-t border-ink-700 pt-3">
        {!replacing ? (
          <button className="btn-secondary" onClick={() => setReplacing(true)} data-testid="replace-reference">
            参照顔を差し替える（新版を作成）
          </button>
        ) : (
          <div className="space-y-3">
            <div className="rounded border border-amber-700 bg-amber-950/50 p-2 text-xs text-amber-100">
              <strong>この操作はキャラクターの同一性を変えます。</strong> 新しい参照顔で作った画像は、これまでの画像と別人になる可能性があります。
              旧版は保持され、いつでもロールバックできます。
            </div>
            <Dropzone onFiles={upload} />
            {analysis && (
              <div className="flex flex-wrap gap-2">
                {analysis.items.map((item) => (
                  <label key={item.image_id} className={`card w-28 cursor-pointer p-1 ${chosen === item.image_id ? "border-accent" : ""} ${!item.usable ? "opacity-50" : ""}`}>
                    <img src={item.thumb_url} alt={item.filename} className="aspect-[3/4] w-full rounded object-cover" />
                    <div className="mt-1 flex items-center justify-between">
                      <input type="radio" name="new-ref" disabled={!item.usable} checked={chosen === item.image_id} onChange={() => setChosen(item.image_id)} />
                      <QualityBadge quality={item.quality} />
                    </div>
                  </label>
                ))}
              </div>
            )}
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="メモ（例: 正面の良い1枚に差し替え）" />
            <div className="flex gap-2">
              <button className="btn-primary" disabled={!chosen} onClick={() => setConfirm(true)}>
                新版として保存
              </button>
              <button className="btn-secondary" onClick={() => setReplacing(false)}>
                やめる
              </button>
            </div>
          </div>
        )}
      </div>
      <ConfirmDialog open={confirm} title="参照顔を差し替えて新版を作成" confirmLabel="新版を作成" onCancel={() => setConfirm(false)} onConfirm={createVersion}>
        <p>以降の生成はこの新しい参照顔で行われます。これまでの版で作った画像との同一性は保証されません。</p>
      </ConfirmDialog>
    </Modal>
  );
}
