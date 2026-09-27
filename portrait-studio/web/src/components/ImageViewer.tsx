import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, getErrorMessage, unwrap, type ImageItem } from "../api/client";
import { useImage, useInvalidateAfterJob, usePatchImage } from "../api/queries";
import { copyText, formatDate, METHOD_LABEL } from "../lib/format";
import { useUiStore } from "../store/ui";
import { SimilarityBadge } from "./Badges";
import { BlurImage } from "./BlurImage";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

interface Props {
  images: ImageItem[];
  index: number | null;
  onClose: () => void;
  onIndexChange: (index: number) => void;
}

/**
 * Lightbox with full parameters. Keys: ← → move, S favorite, C compare tray, Esc close.
 * Actions: regenerate (same params), copy params, pin params into the workspace, recompute similarity, delete.
 */
export function ImageViewer({ images, index, onClose, onIndexChange }: Props) {
  const image = index !== null ? images[index] : undefined;
  const { data: detail } = useImage(image?.id);
  const patch = usePatchImage();
  const toast = useToast();
  const navigate = useNavigate();
  const invalidate = useInvalidateAfterJob();
  const toggleTray = useUiStore((s) => s.toggleTray);
  const inTray = useUiStore((s) => (image ? s.compareTray.includes(image.id) : false));
  const setPinned = useUiStore((s) => s.setPinnedParams);
  const [busy, setBusy] = useState(false);

  const move = useCallback(
    (delta: number) => {
      if (index === null) return;
      const next = index + delta;
      if (next >= 0 && next < images.length) onIndexChange(next);
    },
    [index, images.length, onIndexChange],
  );

  useEffect(() => {
    if (index === null) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "ArrowRight") move(1);
      else if (e.key === "ArrowLeft") move(-1);
      else if (e.key.toLowerCase() === "s" && image) patch.mutate({ id: image.id, body: { favorite: !image.favorite } });
      else if (e.key.toLowerCase() === "c" && image) toggleTray(image.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, move, image, patch, toggleTray]);

  const snap = useMemo(() => (detail?.params_snapshot ?? {}) as Record<string, unknown>, [detail]);
  if (!image) return null;

  const regenerate = async (keepSeed: boolean) => {
    setBusy(true);
    try {
      const r = unwrap(await api.POST("/api/images/{image_id}/regenerate", { params: { path: { image_id: image.id } }, body: { keep_seed: keepSeed, count: 1 } }));
      toast.success(`再生成をキューに追加しました（位置 ${r.position}）`);
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const recompute = async () => {
    try {
      const r = unwrap(await api.GET("/api/images/{image_id}/similarity", { params: { path: { image_id: image.id } } }));
      toast.info(r.similarity === null ? `類似度: ${r.reason ?? "顔検出不可"}` : `類似度 ${r.similarity.toFixed(3)}（${r.grade}）`);
      invalidate();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const pin = () => {
    if (!image.character_id) return;
    setPinned({
      characterId: image.character_id,
      prompt: String(snap.prompt ?? ""),
      negative_prompt: (snap.negative_prompt as string | null) ?? null,
      scene_ids: (snap.scene_ids as string[]) ?? [],
      width: (snap.width as number) ?? null,
      height: (snap.height as number) ?? null,
      upscale: (snap.upscale as number) ?? null,
      face_detailer: (snap.face_detailer as boolean) ?? null,
      steps: (snap.steps as number) ?? null,
      cfg: (snap.cfg as number) ?? null,
      sampler_name: (snap.sampler_name as string) ?? null,
      scheduler: (snap.scheduler as string) ?? null,
      seed: (snap.seed as number) ?? -1,
    });
    onClose();
    navigate(`/workspace/${image.character_id}`);
  };
  const remove = async () => {
    try {
      unwrap(await api.DELETE("/api/images/{image_id}", { params: { path: { image_id: image.id } } }));
      toast.info("画像を削除しました（論理削除）");
      invalidate();
      onClose();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  const rows: Array<[string, unknown]> = [
    ["seed", snap.seed],
    ["手法", snap.face_method ? METHOD_LABEL[String(snap.face_method)] ?? snap.face_method : "—"],
    ["face_weight", snap.face_weight],
    ["checkpoint", snap.checkpoint],
    ["解像度", snap.width && snap.height ? `${snap.width}×${snap.height}` : "—"],
    ["upscale", snap.upscale],
    ["steps / cfg", snap.steps !== undefined ? `${snap.steps} / ${snap.cfg}` : "—"],
    ["sampler", snap.sampler_name ? `${snap.sampler_name} / ${snap.scheduler}` : "—"],
    ["face_detailer", snap.face_detailer ? `on (denoise ${snap.face_detailer_denoise})` : "off"],
    ["版", image.character_version ? `v${image.character_version}` : "—"],
    ["生成日時", formatDate(image.created_at)],
  ];

  return (
    <Modal open onClose={onClose} title={`画像 ${index! + 1} / ${images.length}`} wide testId="image-viewer">
      <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        <div className="relative">
          <BlurImage id={image.id} src={image.file_url} alt={String(snap.prompt ?? "生成画像")} className="max-h-[75vh] w-full rounded" imgClassName="!object-contain" loading="eager" />
          <div className="mt-2 flex justify-between text-xs text-slate-400">
            <button className="btn-ghost" onClick={() => move(-1)} disabled={index === 0}>
              ← 前
            </button>
            <span className="space-x-1">
              <span className="kbd">←→</span> 移動 <span className="kbd">S</span> 保存 <span className="kbd">C</span> 比較 <span className="kbd">Esc</span> 閉じる
            </span>
            <button className="btn-ghost" onClick={() => move(1)} disabled={index === images.length - 1}>
              次 →
            </button>
          </div>
        </div>
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <SimilarityBadge value={image.similarity} status={image.similarity_status} />
            {image.character_name && <span className="badge bg-ink-700 text-slate-200">{image.character_name}</span>}
            {image.favorite && <span className="badge bg-amber-900 text-amber-100">★ お気に入り</span>}
          </div>
          <div>
            <div className="label">プロンプト</div>
            <p className="whitespace-pre-wrap break-words rounded bg-ink-800 p-2 text-xs">{String(snap.prompt ?? "") || "（シーンのみ）"}</p>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            {rows.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-slate-400">{k}</dt>
                <dd className="truncate font-mono" title={String(v ?? "")}>
                  {v === undefined || v === null ? "—" : String(v)}
                </dd>
              </div>
            ))}
          </dl>
          <details className="text-xs">
            <summary className="cursor-pointer text-slate-400">▸ 完全な params_snapshot</summary>
            <pre className="mt-1 max-h-48 overflow-auto rounded bg-ink-950 p-2 font-mono text-[10px]">{JSON.stringify(snap, null, 2)}</pre>
          </details>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={busy || !image.character_id} onClick={() => regenerate(true)} data-testid="regenerate-same">
              同一パラメータで再生成
            </button>
            <button className="btn-secondary" disabled={busy || !image.character_id} onClick={() => regenerate(false)}>
              seed を変えて再生成
            </button>
            <button className="btn-secondary" onClick={() => copyText(JSON.stringify(snap, null, 2)).then(() => toast.success("パラメータをコピーしました"))}>
              パラメータをコピー
            </button>
            <button className="btn-secondary" disabled={!image.character_id} onClick={pin}>
              この条件で固定
            </button>
            <button className="btn-secondary" onClick={recompute}>
              類似度を再計算
            </button>
            <button className="btn-secondary" onClick={() => patch.mutate({ id: image.id, body: { favorite: !image.favorite } })} aria-pressed={image.favorite}>
              {image.favorite ? "★ お気に入り解除" : "☆ お気に入り"}
            </button>
            <button className={inTray ? "btn-primary" : "btn-secondary"} onClick={() => toggleTray(image.id)}>
              {inTray ? "比較トレイから外す" : "比較トレイに追加"}
            </button>
            <button className="btn-danger" onClick={remove}>
              削除
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
