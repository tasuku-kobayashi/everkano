import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { useNavigate, useParams } from "react-router-dom";
import { api, getErrorMessage, unwrap, type CharacterDetail, type ImageItem, type LockedPatch, type PreviewVram } from "../api/client";
import { isFinished, useCharacter, useCharacters, useHealth, useImages, useJob, usePatchImage, useScenes, useWorkflowJson } from "../api/queries";
import { SimilarityBadge } from "../components/Badges";
import { ImageCard } from "../components/ImageCard";
import { ImageViewer } from "../components/ImageViewer";
import { JobRow } from "../components/JobBar";
import { LockedEditor } from "../components/LockedEditor";
import { LockedPanel } from "../components/LockedPanel";
import { useToast } from "../components/Toast";
import { useDebounce } from "../hooks/useDebounce";
import { copyText, formatRelative, lowerResolution, METHOD_LABEL } from "../lib/format";
import { composePrompt } from "../lib/prompt";
import { useUiStore } from "../store/ui";
import { VersionsDialog } from "./VersionsDialog";

interface Form {
  prompt: string;
  negative: string;
  count: number;
  seed: number;
  width: number | null;
  height: number | null;
  upscale: number;
  faceDetailer: boolean;
  steps: number | null;
  cfg: number | null;
  sampler: string;
  scheduler: string;
  sceneIds: string[];
}

const DEFAULT_FORM: Form = { prompt: "", negative: "", count: 4, seed: -1, width: null, height: null, upscale: 1.0, faceDetailer: true, steps: null, cfg: null, sampler: "", scheduler: "", sceneIds: [] };

/** Screen 3: three columns — character card / generation / history. Progressive disclosure by default. */
export function WorkspacePage() {
  const { characterId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { data: characters } = useCharacters({ sort: "recent" });
  const { data: character } = useCharacter(characterId);
  const touch = useUiStore((s) => s.touchCharacter);
  const recent = useUiStore((s) => s.recentCharacters);
  const pinned = useUiStore((s) => s.pinnedParams);
  const setPinned = useUiStore((s) => s.setPinnedParams);

  const [form, setForm] = useState<Form>(DEFAULT_FORM);
  const [showDetails, setShowDetails] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrides, setOverrides] = useState<LockedPatch>({});
  const [saveAsVersion, setSaveAsVersion] = useState(false);
  const [jobId, setJobId] = useState<string | undefined>();
  const [versionsOpen, setVersionsOpen] = useState(false);

  useEffect(() => {
    if (characterId) touch(characterId);
  }, [characterId, touch]);

  // no character in the URL: open the most recent one
  useEffect(() => {
    if (!characterId && characters?.length) navigate(`/workspace/${(recent.find((id) => characters.some((c) => c.id === id)) ?? characters[0]!.id)}`, { replace: true });
  }, [characterId, characters, recent, navigate]);

  // "この条件で固定" from the viewer
  useEffect(() => {
    if (pinned && pinned.characterId === characterId) {
      setForm((f) => ({
        ...f,
        prompt: pinned.prompt,
        negative: pinned.negative_prompt ?? "",
        sceneIds: pinned.scene_ids,
        width: pinned.width,
        height: pinned.height,
        upscale: pinned.upscale ?? 1.0,
        faceDetailer: pinned.face_detailer ?? true,
        steps: pinned.steps,
        cfg: pinned.cfg,
        sampler: pinned.sampler_name ?? "",
        scheduler: pinned.scheduler ?? "",
        seed: pinned.seed,
      }));
      setShowDetails(true);
      setPinned(null);
      toast.info("画像のパラメータを読み込みました（seed も固定）");
    }
  }, [pinned, characterId, setPinned, toast]);

  if (!characterId) {
    return (
      <div className="p-8 text-center text-sm text-slate-400">
        キャラクターがありません。<button className="btn-primary ml-2" onClick={() => navigate("/create")}>新規キャラクター作成</button>
      </div>
    );
  }

  return (
    <div className="grid h-full grid-cols-[280px_minmax(0,1fr)_300px] gap-0" data-testid="workspace">
      <LeftColumn
        character={character}
        characters={characters ?? []}
        recent={recent}
        onSwitch={(id) => navigate(`/workspace/${id}`)}
        overrideOpen={overrideOpen}
        setOverrideOpen={setOverrideOpen}
        overrides={overrides}
        setOverrides={setOverrides}
        saveAsVersion={saveAsVersion}
        setSaveAsVersion={setSaveAsVersion}
        onVersions={() => setVersionsOpen(true)}
      />
      <CenterColumn
        character={character}
        form={form}
        setForm={setForm}
        showDetails={showDetails}
        setShowDetails={setShowDetails}
        showAdvanced={showAdvanced}
        setShowAdvanced={setShowAdvanced}
        overrides={overrideOpen ? overrides : {}}
        saveAsVersion={overrideOpen && saveAsVersion}
        jobId={jobId}
        setJobId={setJobId}
      />
      <RightColumn characterId={characterId} />
      {versionsOpen && <VersionsDialog characterId={characterId} onClose={() => setVersionsOpen(false)} />}
    </div>
  );
}

// ----------------------------------------------------------------------------- left
function LeftColumn(props: {
  character: CharacterDetail | undefined;
  characters: Array<{ id: string; name: string }>;
  recent: string[];
  onSwitch: (id: string) => void;
  overrideOpen: boolean;
  setOverrideOpen: (v: boolean) => void;
  overrides: LockedPatch;
  setOverrides: (v: LockedPatch) => void;
  saveAsVersion: boolean;
  setSaveAsVersion: (v: boolean) => void;
  onVersions: () => void;
}) {
  const { character, characters, recent, onSwitch, overrideOpen, setOverrideOpen, overrides, setOverrides, saveAsVersion, setSaveAsVersion, onVersions } = props;
  const health = useHealth();
  const primary = character?.references.find((r) => r.is_primary) ?? character?.references[0];
  return (
    <aside className="flex flex-col gap-3 overflow-auto border-r border-ink-700 p-3" data-testid="workspace-left">
      <div>
        <label className="label">キャラクター</label>
        <select className="input" value={character?.id ?? ""} onChange={(e) => onSwitch(e.target.value)} aria-label="キャラクターを切り替え" data-testid="character-select">
          {characters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        {recent.length > 1 && (
          <div className="mt-1 flex flex-wrap gap-1" data-testid="recent-characters">
            {recent
              .map((id) => characters.find((c) => c.id === id))
              .filter((c): c is { id: string; name: string } => !!c)
              .slice(0, 5)
              .map((c) => (
                <button key={c.id} className={clsx("badge", c.id === character?.id ? "bg-accent text-white" : "bg-ink-700 text-slate-200 hover:bg-ink-600")} onClick={() => onSwitch(c.id)}>
                  {c.name}
                </button>
              ))}
          </div>
        )}
      </div>
      {character ? (
        <>
          <div className="card p-2">
            {primary && <img src={primary.file_url} alt={`${character.name} の参照顔`} className="aspect-[3/4] w-full rounded object-cover" />}
            <div className="mt-2 flex items-center justify-between">
              <div className="font-medium">{character.name}</div>
              <span className="badge bg-ink-700 text-slate-200">v{character.current_version}</span>
            </div>
            <div className="flex gap-1 pt-1">
              {character.references.map((r, i) => (
                <img key={r.id} src={r.file_url} alt={`参照顔 ${i + 1}`} className={clsx("h-10 w-10 rounded object-cover", r.is_primary && "ring-2 ring-emerald-500")} />
              ))}
            </div>
          </div>
          {character.locked && (
            <div className="card p-2">
              <LockedPanel locked={character.locked} version={character.current_version} />
              <div className="mt-2 flex flex-col gap-1">
                <button className={overrideOpen ? "btn-primary" : "btn-secondary"} onClick={() => setOverrideOpen(!overrideOpen)} data-testid="toggle-override" aria-expanded={overrideOpen}>
                  {overrideOpen ? "設定の変更をやめる" : "設定を変更"}
                </button>
                <button className="btn-secondary" onClick={onVersions}>
                  参照顔を差し替え / 版を管理
                </button>
              </div>
              {overrideOpen && (
                <div className="mt-2 space-y-2" data-testid="override-panel">
                  <div className="rounded border border-rose-700 bg-rose-950/60 p-2 text-xs text-rose-100" data-testid="override-warning">
                    <strong>この操作はキャラクターの同一性を変えます。</strong> 変更すると以降の生成は別人になる可能性があります。変更は既定で保存されず、この生成限りです。
                  </div>
                  <LockedEditor value={{ ...character.locked, ...overrides }} onChange={setOverrides} checkpoints={health.data?.checkpoints ?? []} faceMethods={health.data?.face_methods ?? []} showResolution={false} />
                  <label className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={saveAsVersion} onChange={(e) => setSaveAsVersion(e.target.checked)} />
                    新版として保存する（旧版は保持）
                  </label>
                </div>
              )}
            </div>
          )}
        </>
      ) : (
        <div className="skeleton h-64" />
      )}
    </aside>
  );
}

// ----------------------------------------------------------------------------- center
function CenterColumn(props: {
  character: CharacterDetail | undefined;
  form: Form;
  setForm: React.Dispatch<React.SetStateAction<Form>>;
  showDetails: boolean;
  setShowDetails: (v: boolean) => void;
  showAdvanced: boolean;
  setShowAdvanced: (v: boolean) => void;
  overrides: LockedPatch;
  saveAsVersion: boolean;
  jobId: string | undefined;
  setJobId: (id: string | undefined) => void;
}) {
  const { character, form, setForm, showDetails, setShowDetails, showAdvanced, setShowAdvanced, overrides, saveAsVersion, jobId, setJobId } = props;
  const toast = useToast();
  const { data: scenes } = useScenes();
  const { data: job } = useJob(jobId);
  const { data: results } = useImages({ job_id: jobId, limit: 8 }, !!jobId && isFinished(job));
  const patch = usePatchImage();
  const [viewer, setViewer] = useState<number | null>(null);
  const [preview, setPreview] = useState<PreviewVram | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const locked = character?.locked;
  const effectiveMethod = overrides.face_method ?? locked?.face_method ?? "pulid";
  const width = form.width ?? locked?.default_width ?? 832;
  const height = form.height ?? locked?.default_height ?? 1216;
  const { data: workflow } = useWorkflowJson(showAdvanced ? effectiveMethod : undefined);

  const changed = useMemo(() => Object.entries(overrides).filter(([k, v]) => v !== null && v !== undefined && locked && (locked as unknown as Record<string, unknown>)[k] !== v), [overrides, locked]);
  const previewKey = useDebounce(`${effectiveMethod}|${width}|${height}|${form.upscale}|${form.faceDetailer}|${form.count}`, 350);

  // OOM pre-warning the moment a dangerous setting is chosen
  useEffect(() => {
    if (!character) return;
    let cancelled = false;
    (async () => {
      try {
        const r = unwrap(await api.POST("/api/generate/preview-vram", { body: { method: effectiveMethod, width, height, count: form.count, upscale: form.upscale, face_detailer: form.faceDetailer } }));
        if (!cancelled) setPreview(r);
      } catch {
        if (!cancelled) setPreview(null);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey, character?.id]);

  const selectedScenes = useMemo(() => (scenes ?? []).filter((s) => form.sceneIds.includes(s.id)), [scenes, form.sceneIds]);

  const generate = useCallback(
    async (patchForm?: Partial<Form>) => {
      if (!character) return;
      const f = { ...form, ...patchForm };
      if (!f.prompt.trim() && !f.sceneIds.length) {
        toast.error("プロンプトかシーンプリセットを入力してください");
        return;
      }
      try {
        const r = unwrap(
          await api.POST("/api/generate", {
            body: {
              character_id: character.id,
              prompt: f.prompt.trim() || selectedScenes.map((s) => s.name).join(" / "),
              negative_prompt: f.negative.trim() || null,
              count: f.count,
              seed: f.seed,
              width: f.width,
              height: f.height,
              upscale: f.upscale,
              face_detailer: f.faceDetailer,
              scene_ids: f.sceneIds,
              steps: f.steps,
              cfg: f.cfg,
              sampler_name: f.sampler || null,
              scheduler: f.scheduler || null,
              overrides: changed.length ? overrides : null,
              allow_locked_override: changed.length > 0,
              save_as_version: saveAsVersion,
              adult_only: true,
            },
          }),
        );
        setJobId(r.job_id);
        if (patchForm) setForm((prev) => ({ ...prev, ...patchForm }));
        toast.info(r.position === 0 ? "生成を開始します" : `キューに追加しました（${r.position} 件待ち）`);
      } catch (e) {
        toast.error(getErrorMessage(e));
      }
    },
    [character, form, selectedScenes, changed, overrides, saveAsVersion, setJobId, setForm, toast],
  );

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void generate();
    }
  };
  const items = results?.items ?? [];
  const failed = job?.status === "error";
  const riskClass = preview?.risk === "high" || preview?.risk === "unknown" ? "border-rose-700 bg-rose-950/60 text-rose-100" : preview?.risk === "medium" ? "border-amber-700 bg-amber-950/60 text-amber-100" : "";

  return (
    <section className="flex min-w-0 flex-col gap-3 overflow-auto p-3" data-testid="workspace-center">
      <div className="card p-3">
        <label className="label" htmlFor="prompt">
          プロンプト（構図・服装・シチュエーション。外見は固定プレフィックスが担当） — <span className="kbd">Enter</span> 生成 / <span className="kbd">Shift+Enter</span> 改行
        </label>
        <textarea id="prompt" ref={textarea} className="input min-h-[72px]" rows={3} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} onKeyDown={onKey} placeholder="standing in a cafe, casual outfit" data-testid="prompt" />
        <div className="mt-2 flex flex-wrap gap-1" data-testid="scene-presets">
          {(scenes ?? [])
            .filter((s) => !s.verify)
            .map((s) => {
              const on = form.sceneIds.includes(s.id);
              return (
                <button key={s.id} className={clsx("badge", on ? "bg-accent text-white" : "bg-ink-700 text-slate-200 hover:bg-ink-600")} onClick={() => setForm({ ...form, sceneIds: on ? form.sceneIds.filter((x) => x !== s.id) : [...form.sceneIds, s.id] })} aria-pressed={on} title={s.description}>
                  {s.name}
                </button>
              );
            })}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            枚数
            <input type="number" min={1} max={8} className="input w-20" value={form.count} onChange={(e) => setForm({ ...form, count: Math.max(1, Math.min(8, Number(e.target.value))) })} data-testid="count" />
          </label>
          <button className="btn-primary text-base" onClick={() => generate()} disabled={!character || (!!job && !isFinished(job))} data-testid="generate">
            生成
          </button>
          {locked && (
            <span className="text-xs text-slate-500">
              {METHOD_LABEL[effectiveMethod ?? "pulid"]} · weight {overrides.face_weight ?? locked.face_weight} · {width}×{height}
              {form.upscale > 1 ? ` ×${form.upscale}` : ""}
            </span>
          )}
          {changed.length > 0 && <span className="badge border border-rose-700 bg-rose-950 text-rose-200">同一性の変更あり: {changed.map(([k]) => k).join(", ")}</span>}
        </div>
        {preview && preview.risk !== "low" && (
          <div className={clsx("mt-2 rounded border p-2 text-xs", riskClass)} data-testid="oom-warning" data-risk={preview.risk}>
            {preview.risk === "unknown" ? "VRAM 見積りが未実測です。" : preview.risk === "high" ? "OOMの可能性が高いです。" : "VRAM に余裕がありません。"}
            {preview.estimated_peak_mb !== null && ` 推定ピーク ${preview.estimated_peak_mb} MB / 空き ${preview.free_mb ?? "?"} MB。`} {preview.advice}
            {preview.would_reject && " この設定では生成は拒否されます。"}
          </div>
        )}
        <div className="mt-2 flex gap-3 text-xs">
          <button className="text-slate-300 hover:underline" onClick={() => setShowDetails(!showDetails)} aria-expanded={showDetails} data-testid="toggle-details">
            {showDetails ? "▾" : "▸"} 詳細
          </button>
          <button className="text-slate-300 hover:underline" onClick={() => setShowAdvanced(!showAdvanced)} aria-expanded={showAdvanced} data-testid="toggle-advanced">
            {showAdvanced ? "▾" : "▸"} 上級
          </button>
        </div>
        {showDetails && locked && (
          <div className="mt-2 grid gap-2 text-xs md:grid-cols-3" data-testid="details-panel">
            <label className="md:col-span-3">
              <span className="label">negative prompt（空ならキャラの既定）</span>
              <textarea className="input font-mono" rows={2} value={form.negative} onChange={(e) => setForm({ ...form, negative: e.target.value })} placeholder={locked.negative_prompt} />
            </label>
            <label>
              <span className="label">seed（-1 = ランダム）</span>
              <input type="number" className="input font-mono" value={form.seed} onChange={(e) => setForm({ ...form, seed: Number(e.target.value) })} data-testid="seed" />
            </label>
            <label>
              <span className="label">steps</span>
              <input type="number" className="input" min={1} max={100} value={form.steps ?? ""} placeholder="既定" onChange={(e) => setForm({ ...form, steps: e.target.value ? Number(e.target.value) : null })} />
            </label>
            <label>
              <span className="label">cfg</span>
              <input type="number" step={0.5} className="input" value={form.cfg ?? ""} placeholder="既定" onChange={(e) => setForm({ ...form, cfg: e.target.value ? Number(e.target.value) : null })} />
            </label>
            <label>
              <span className="label">sampler</span>
              <input className="input" value={form.sampler} placeholder="既定 (dpmpp_2m)" onChange={(e) => setForm({ ...form, sampler: e.target.value })} />
            </label>
            <label>
              <span className="label">scheduler</span>
              <input className="input" value={form.scheduler} placeholder="既定 (karras)" onChange={(e) => setForm({ ...form, scheduler: e.target.value })} />
            </label>
            <div>
              <span className="label">face_weight / detailer denoise（固定）</span>
              <div className="input bg-ink-800 font-mono text-slate-400">
                {overrides.face_weight ?? locked.face_weight} / {overrides.face_detailer_denoise ?? locked.face_detailer_denoise} 🔒
              </div>
            </div>
            <label>
              <span className="label">幅</span>
              <input type="number" step={8} min={512} max={2048} className="input" value={width} onChange={(e) => setForm({ ...form, width: Number(e.target.value) })} data-testid="width" />
            </label>
            <label>
              <span className="label">高さ</span>
              <input type="number" step={8} min={512} max={2048} className="input" value={height} onChange={(e) => setForm({ ...form, height: Number(e.target.value) })} data-testid="height" />
            </label>
            <label>
              <span className="label">hires 倍率（上限 ×{locked.hires_max}）</span>
              <input type="number" step={0.05} min={1} max={locked.hires_max} className="input" value={form.upscale} onChange={(e) => setForm({ ...form, upscale: Number(e.target.value) })} data-testid="upscale" />
            </label>
            <label className="flex items-center gap-2 self-end">
              <input type="checkbox" checked={form.faceDetailer} onChange={(e) => setForm({ ...form, faceDetailer: e.target.checked })} /> face_detailer
            </label>
            <div className="md:col-span-3 text-slate-500">
              合成後のプロンプト: <span className="font-mono">{composePrompt(overrides.prefix_prompt ?? locked.prefix_prompt ?? "", selectedScenes, form.prompt).slice(0, 300)}</span>
            </div>
          </div>
        )}
        {showAdvanced && (
          <div className="mt-2 text-xs" data-testid="advanced-panel">
            <div className="mb-1 flex items-center justify-between">
              <span>ワークフロー: {workflow?.file ?? "…"}（title で差し替え。ノード ID は不使用）</span>
              <button className="btn-secondary" onClick={() => workflow && copyText(JSON.stringify(workflow.nodes, null, 2)).then(() => toast.success("JSON をコピーしました"))}>
                生 JSON をコピー
              </button>
            </div>
            <pre className="max-h-64 overflow-auto rounded bg-ink-950 p-2 font-mono text-[10px]">{workflow ? JSON.stringify(workflow.nodes, null, 2) : "読み込み中…"}</pre>
          </div>
        )}
      </div>

      {job && (
        <div className="card p-3" data-testid="job-card">
          <JobRow
            job={job}
            onCancel={
              !isFinished(job)
                ? async () => {
                    await api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: job.id } } });
                  }
                : undefined
            }
          />
          {failed && (
            <div className="mt-2 space-y-2 text-xs" data-testid="recovery">
              <p className="text-rose-200">失敗: {job.error}</p>
              <div className="flex flex-wrap gap-2">
                <button className="btn-secondary" onClick={() => generate((() => { const [w, h] = lowerResolution(width, height); return { width: w, height: h }; })())}>
                  解像度を 1 段下げて再試行
                </button>
                <button className="btn-secondary" onClick={() => generate({ upscale: 1.0 })} disabled={form.upscale === 1.0}>
                  upscale を 1.0 にして再試行
                </button>
                <button className="btn-secondary" onClick={() => generate({ faceDetailer: false })} disabled={!form.faceDetailer}>
                  face_detailer を切って再試行
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {items.length > 0 && (
        <div>
          <div className="mb-1 text-xs text-slate-400">結果 {items.length} 枚（ホバーで操作。<span className="kbd">S</span> 保存 / <span className="kbd">C</span> 比較）</div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2" data-testid="results">
            {items.map((img, i) => (
              <ImageCard
                key={img.id}
                image={img}
                onOpen={() => setViewer(i)}
                onFavorite={() => patch.mutate({ id: img.id, body: { favorite: !img.favorite } })}
                onRegenerate={() => regenerate(img, toast)}
                onSimilarity={() => recompute(img, toast)}
              />
            ))}
          </div>
        </div>
      )}
      <ImageViewer images={items} index={viewer} onClose={() => setViewer(null)} onIndexChange={setViewer} />
    </section>
  );
}

async function regenerate(img: ImageItem, toast: ReturnType<typeof useToast>) {
  try {
    const r = unwrap(await api.POST("/api/images/{image_id}/regenerate", { params: { path: { image_id: img.id } }, body: { keep_seed: true, count: 1 } }));
    toast.success(`同一パラメータで再生成をキューに追加しました（位置 ${r.position}）`);
  } catch (e) {
    toast.error(getErrorMessage(e));
  }
}

async function recompute(img: ImageItem, toast: ReturnType<typeof useToast>) {
  try {
    const r = unwrap(await api.GET("/api/images/{image_id}/similarity", { params: { path: { image_id: img.id } } }));
    toast.info(r.similarity === null ? `類似度: ${r.reason}` : `類似度 ${r.similarity.toFixed(3)}`);
  } catch (e) {
    toast.error(getErrorMessage(e));
  }
}

// ----------------------------------------------------------------------------- right
function RightColumn({ characterId }: { characterId: string }) {
  const { data } = useImages({ character_id: characterId, kind: "generated", limit: 200 });
  const toast = useToast();
  const [viewer, setViewer] = useState<number | null>(null);
  const items = data?.items ?? [];
  return (
    <aside className="flex flex-col overflow-auto border-l border-ink-700 p-2" data-testid="workspace-history">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
        <span>このキャラの生成履歴</span>
        <span>{data?.total ?? 0} 枚</span>
      </div>
      <ul className="space-y-1">
        {items.map((img, i) => (
          <li key={img.id} className="card flex gap-2 p-1" data-testid="history-item">
            <button className="shrink-0" onClick={() => setViewer(i)} aria-label="画像を開く">
              <img src={img.thumb_url} alt="" className={clsx("h-16 w-12 rounded object-cover", useUiStore.getState().blurDefault && !useUiStore.getState().revealed[img.id] && "blur-nsfw")} loading="lazy" />
            </button>
            <div className="min-w-0 flex-1 text-[11px]">
              <div className="flex items-center justify-between">
                <span className="font-mono text-slate-300">seed {img.seed}</span>
                <span className="badge bg-ink-700 text-slate-300">v{img.character_version}</span>
              </div>
              <div className="mt-0.5">
                <SimilarityBadge value={img.similarity} status={img.similarity_status} compact />
              </div>
              <div className="mt-0.5 flex items-center justify-between text-slate-500">
                <span>{formatRelative(img.created_at)}</span>
                <button className="text-accent hover:underline" onClick={() => regenerate(img, toast)} title="同一パラメータで再生成">
                  再生成
                </button>
              </div>
            </div>
          </li>
        ))}
        {items.length === 0 && <li className="p-2 text-xs text-slate-500">まだ生成していません</li>}
      </ul>
      <ImageViewer images={items} index={viewer} onClose={() => setViewer(null)} onIndexChange={setViewer} />
    </aside>
  );
}
