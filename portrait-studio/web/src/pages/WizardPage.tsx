import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { useNavigate } from "react-router-dom";
import { api, getErrorMessage, unwrap, type AnalyzeResponse, type CharacterDetail, type FaceMethod, type LockedPatch } from "../api/client";
import { isFinished, useHealth, useImages, useJob, useScenes } from "../api/queries";
import { SimilarityBadge } from "../components/Badges";
import { BlurImage } from "../components/BlurImage";
import { Dropzone } from "../components/Dropzone";
import { JobRow } from "../components/JobBar";
import { LockedEditor } from "../components/LockedEditor";
import { QualityCard } from "../components/QualityCard";
import { Stepper } from "../components/Stepper";
import { useToast } from "../components/Toast";
import { METHOD_LABEL } from "../lib/format";
import { parseTags } from "../lib/prompt";

const STEPS = ["種となる顔", "参照顔の選定", "同一性の検証", "登録", "完了"];
const DEFAULT_SEED_PROMPT = "japanese woman in her late 20s, portrait, looking at the camera, neutral expression, natural light, plain background";
const DEFAULT_WEIGHTS = [0.6, 0.8, 1.0];
const VERIFY_SCENES = ["portrait_closeup", "upper_body_cafe", "full_body_street"];

const NOTICE = (
  <div className="rounded border border-amber-700 bg-amber-950/60 p-2 text-xs text-amber-100" data-testid="synthetic-notice">
    参照するのは<strong>架空のキャラクターの顔</strong>です。実在の人物の写真は使用しないでください。生成・登録できるのは成人キャラクターのみです。
  </div>
);

/** Screen 2: 5-step creation wizard (exploration → convergence). Separate from the workspace on purpose. */
export function WizardPage() {
  const [step, setStep] = useState(0);
  const health = useHealth();
  const faceMethods = (health.data?.face_methods ?? []) as FaceMethod[];
  const checkpoints = health.data?.checkpoints ?? [];

  // step 1
  const [candidateIds, setCandidateIds] = useState<string[]>([]);
  // step 2
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [primary, setPrimary] = useState<string | null>(null);
  // step 3
  const [draft, setDraft] = useState<CharacterDetail | null>(null);
  const [chosen, setChosen] = useState<{ method: FaceMethod; weight: number } | null>(null);
  // step 4/5
  const [registered, setRegistered] = useState<CharacterDetail | null>(null);

  return (
    <div className="flex h-full flex-col">
      <Stepper steps={STEPS} current={step} onSelect={(i) => i < step && i !== 4 && setStep(i)} />
      <div className="flex-1 overflow-auto p-4">
        {step === 0 && (
          <StepSeed
            candidateIds={candidateIds}
            onCandidates={(ids) => setCandidateIds(Array.from(new Set([...candidateIds, ...ids])))}
            onRemove={(id) => setCandidateIds(candidateIds.filter((x) => x !== id))}
            onNext={() => setStep(1)}
          />
        )}
        {step === 1 && (
          <StepSelect
            candidateIds={candidateIds}
            analysis={analysis}
            setAnalysis={setAnalysis}
            selected={selected}
            setSelected={setSelected}
            primary={primary}
            setPrimary={setPrimary}
            faceMethod={faceMethods[0] ?? "pulid"}
            onBack={() => setStep(0)}
            onDraft={(d) => {
              setDraft(d);
              setStep(2);
            }}
          />
        )}
        {step === 2 && draft && (
          <StepVerify
            draft={draft}
            faceMethods={faceMethods.length ? faceMethods : (["pulid"] as FaceMethod[])}
            onBack={() => setStep(1)}
            onLock={(method, weight) => {
              setChosen({ method, weight });
              setStep(3);
            }}
          />
        )}
        {step === 3 && draft && (
          <StepRegister
            draft={draft}
            chosen={chosen}
            checkpoints={checkpoints}
            faceMethods={faceMethods}
            onBack={() => setStep(2)}
            onRegistered={(c) => {
              setRegistered(c);
              setStep(4);
            }}
          />
        )}
        {step === 4 && registered && (
          <StepDone
            character={registered}
            onAnother={() => {
              setStep(0);
              setCandidateIds([]);
              setAnalysis(null);
              setSelected([]);
              setPrimary(null);
              setDraft(null);
              setChosen(null);
              setRegistered(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Step 1
function StepSeed({ candidateIds, onCandidates, onRemove, onNext }: { candidateIds: string[]; onCandidates: (ids: string[]) => void; onRemove: (id: string) => void; onNext: () => void }) {
  const toast = useToast();
  const [prompt, setPrompt] = useState(DEFAULT_SEED_PROMPT);
  const [count, setCount] = useState(6);
  const [jobId, setJobId] = useState<string | undefined>();
  const { data: job } = useJob(jobId);
  const { data: draftImages } = useImages({ job_id: jobId, kind: "draft", limit: 8 }, !!jobId && isFinished(job));
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (job && job.status === "error") toast.error(`種顔の生成に失敗しました: ${job.error}`);
  }, [job, toast]);

  const generate = async () => {
    try {
      const r = unwrap(await api.POST("/api/characters/draft", { body: { prompt, count } }));
      setJobId(r.job_id);
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const upload = async (files: File[]) => {
    setUploading(true);
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    try {
      const r = unwrap(await api.POST("/api/characters/analyze", { body: form as unknown as { files?: string[] } }));
      onCandidates(r.items.map((i) => i.image_id));
      const unusable = r.items.filter((i) => !i.usable).length;
      toast.success(`${r.items.length} 枚を候補に追加しました${unusable ? `（顔が検出できない画像 ${unusable} 枚は使用不可）` : ""}`);
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setUploading(false);
    }
  };
  const draftIds = job?.result_image_ids ?? [];
  const allDraftsAdded = draftIds.length > 0 && draftIds.every((id) => candidateIds.includes(id));

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      {NOTICE}
      <div className="grid gap-4 md:grid-cols-2">
        <section className="card p-4" aria-labelledby="seed-text">
          <h2 id="seed-text" className="mb-2 font-semibold">
            (a) テキストから生成
          </h2>
          <label className="label">種プロンプト（外見の固定プレフィックスは自動で付きます）</label>
          <textarea className="input font-mono text-xs" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} data-testid="seed-prompt" />
          <div className="mt-2 flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              枚数
              <input type="number" min={4} max={8} className="input w-20" value={count} onChange={(e) => setCount(Math.max(1, Math.min(8, Number(e.target.value))))} />
            </label>
            <button className="btn-primary" onClick={generate} disabled={!prompt.trim() || (!!job && !isFinished(job))} data-testid="seed-generate">
              種顔を生成
            </button>
          </div>
          {job && !isFinished(job) && (
            <div className="mt-3">
              <JobRow job={job} compact />
            </div>
          )}
          {draftImages && draftImages.items.length > 0 && (
            <div className="mt-3">
              <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
                <span>生成結果 — 候補に追加する画像を選んでください</span>
                <button className="btn-ghost" onClick={() => onCandidates(draftIds)} disabled={allDraftsAdded}>
                  すべて追加
                </button>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {draftImages.items.map((img) => {
                  const added = candidateIds.includes(img.id);
                  return (
                    <button
                      key={img.id}
                      className={clsx("card overflow-hidden text-left", added && "ring-2 ring-accent")}
                      onClick={() => (added ? onRemove(img.id) : onCandidates([img.id]))}
                      aria-pressed={added}
                      data-testid="draft-result"
                    >
                      <BlurImage id={img.id} src={img.thumb_url} alt={`種顔 seed ${img.seed}`} className="aspect-[3/4]" />
                      <div className="flex justify-between px-1 py-0.5 text-[10px] text-slate-400">
                        <span className="font-mono">seed {img.seed}</span>
                        <span>{added ? "候補 ✓" : "追加"}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </section>
        <section className="card p-4" aria-labelledby="seed-upload">
          <h2 id="seed-upload" className="mb-2 font-semibold">
            (b) 画像をアップロード
          </h2>
          <Dropzone onFiles={upload} disabled={uploading} />
          <p className="mt-2 text-xs text-slate-500">正面・高解像度・素の表情の画像が最良の結果になります。アップロードと同時に顔検出と品質分析を行います。</p>
        </section>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-400" data-testid="candidate-count">
          候補 {candidateIds.length} 枚
        </span>
        <button className="btn-primary" disabled={candidateIds.length === 0} onClick={onNext} data-testid="to-step-2">
          次へ: 参照顔の選定 →
        </button>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Step 2
function StepSelect(props: {
  candidateIds: string[];
  analysis: AnalyzeResponse | null;
  setAnalysis: (a: AnalyzeResponse) => void;
  selected: string[];
  setSelected: (ids: string[]) => void;
  primary: string | null;
  setPrimary: (id: string | null) => void;
  faceMethod: FaceMethod;
  onBack: () => void;
  onDraft: (d: CharacterDetail) => void;
}) {
  const { candidateIds, analysis, setAnalysis, selected, setSelected, primary, setPrimary, faceMethod, onBack, onDraft } = props;
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const key = candidateIds.join(",");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const form = new FormData();
        form.append("image_ids", key);
        const r = unwrap(await api.POST("/api/characters/analyze", { body: form as unknown as { image_ids?: string | null } }));
        if (cancelled) return;
        setAnalysis(r);
        if (r.recommended_index !== null) {
          const rec = r.items[r.recommended_index];
          if (rec) {
            setSelected([rec.image_id]);
            setPrimary(rec.image_id);
          }
        }
      } catch (e) {
        if (!cancelled) toast.error(getErrorMessage(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const clusterOf = useMemo(() => {
    const map = new Map<number, number>();
    analysis?.clusters.forEach((group, ci) => group.forEach((i) => map.set(i, ci)));
    return map;
  }, [analysis]);

  const confirm = async () => {
    if (!primary || !selected.length) return;
    setBusy(true);
    try {
      const d = unwrap(await api.POST("/api/characters", { body: { draft: true, reference_image_ids: selected, primary_image_id: primary, locked: { face_method: faceMethod } } }));
      onDraft(d);
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (!analysis) {
    return (
      <div className="mx-auto max-w-6xl">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
          {candidateIds.map((id) => (
            <div key={id} className="skeleton aspect-[3/5]" />
          ))}
        </div>
        <p className="mt-2 text-sm text-slate-400">顔を検出し、品質を測定しています…</p>
      </div>
    );
  }
  const rec = analysis.recommended_index !== null ? analysis.items[analysis.recommended_index] : undefined;
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      {NOTICE}
      <div className="card p-3 text-sm" data-testid="recommendation">
        {rec ? (
          <>
            <span className="badge mr-2 bg-emerald-600 text-white">推奨</span>
            <strong>{rec.filename}</strong> — {analysis.recommend_reason}
          </>
        ) : (
          <span className="text-rose-300">{analysis.recommend_reason}</span>
        )}
        <div className="mt-1 text-xs text-slate-400">
          同じ顔のグループ: {analysis.clusters.filter((g) => g.length > 1).length} 組 / 候補 {analysis.items.length} 枚 ·
          しきい値: 顔サイズ比 ≥ {analysis.thresholds.face_ratio_min}, |yaw| ≤ {analysis.thresholds.yaw_max_deg}°, 信頼度 ≥ {analysis.thresholds.det_score_min}
        </div>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-3">
        {analysis.items.map((item, i) => (
          <QualityCard
            key={item.image_id}
            item={item}
            selected={selected.includes(item.image_id)}
            primary={primary === item.image_id}
            recommended={analysis.recommended_index === i}
            clusterIndex={item.usable ? clusterOf.get(i) ?? null : null}
            thresholds={analysis.thresholds}
            onToggle={() => {
              const next = selected.includes(item.image_id) ? selected.filter((x) => x !== item.image_id) : [...selected, item.image_id];
              setSelected(next);
              if (!next.includes(primary ?? "")) setPrimary(next[0] ?? null);
            }}
            onPrimary={() => setPrimary(item.image_id)}
          />
        ))}
      </div>
      {analysis.items.length > 1 && (
        <details className="card p-3 text-xs">
          <summary className="cursor-pointer">▸ 類似度行列（ArcFace コサイン）</summary>
          <table className="mt-2 font-mono">
            <tbody>
              {analysis.similarity_matrix.map((row, i) => (
                <tr key={i}>
                  <th className="pr-2 text-left text-slate-400">#{i}</th>
                  {row.map((v, j) => (
                    <td key={j} className={clsx("px-1", v !== null && v >= analysis.thresholds.cluster! && i !== j && "text-emerald-300")}>
                      {v === null ? "—" : v.toFixed(2)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <div className="flex items-center justify-between">
        <button className="btn-secondary" onClick={onBack}>
          ← 戻る
        </button>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-400">
            選択 {selected.length} 枚{primary ? "（primary あり）" : ""}
          </span>
          <button className="btn-primary" disabled={!primary || busy} onClick={confirm} data-testid="confirm-reference">
            参照顔として確定 →
          </button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Step 3
interface VerifyItem {
  image_id: string;
  scene: string;
  face_weight: number;
  similarity: number | null;
  similarity_status?: string;
  seed?: number;
}

function StepVerify({ draft, faceMethods, onBack, onLock }: { draft: CharacterDetail; faceMethods: FaceMethod[]; onBack: () => void; onLock: (method: FaceMethod, weight: number) => void }) {
  const toast = useToast();
  const { data: scenes } = useScenes();
  const [method, setMethod] = useState<FaceMethod>(faceMethods[0] ?? "pulid");
  const [weights, setWeights] = useState<number[]>(DEFAULT_WEIGHTS);
  const [jobs, setJobs] = useState<Partial<Record<FaceMethod, string>>>({});
  const jobId = jobs[method];
  const { data: job } = useJob(jobId);
  const primaryRef = draft.references.find((r) => r.is_primary) ?? draft.references[0];

  const run = async () => {
    try {
      const r = unwrap(await api.POST("/api/characters/{character_id}/verify", { params: { path: { character_id: draft.id } }, body: { face_method: method, face_weights: weights, scenes: VERIFY_SCENES } }));
      setJobs((prev) => ({ ...prev, [method]: r.job_id }));
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const items = ((job?.result as { items?: VerifyItem[] } | null)?.items ?? []) as VerifyItem[];
  const byWeight = ((job?.result as { by_weight?: Record<string, number> } | null)?.by_weight ?? {}) as Record<string, number>;
  const sceneName = (id: string) => scenes?.find((s) => s.id === id)?.name ?? id;

  return (
    <div className="grid gap-4 md:grid-cols-[260px_minmax(0,1fr)]">
      <aside className="md:sticky md:top-14 md:self-start" data-testid="verify-reference">
        <div className="card p-3">
          <div className="mb-1 text-xs text-emerald-300">参照顔（固定表示）</div>
          {primaryRef && <img src={primaryRef.file_url} alt="参照顔" className="aspect-[3/4] w-full rounded object-cover" />}
          <div className="mt-2 text-xs text-slate-400">下書き ID: {draft.id.slice(-8)}</div>
          <div className="mt-3 text-xs text-slate-300">
            <div className="label">手法</div>
            <div className="flex flex-wrap gap-1" role="tablist">
              {faceMethods.map((m) => (
                <button key={m} role="tab" aria-selected={m === method} className={clsx("btn text-xs", m === method ? "btn-primary" : "btn-secondary")} onClick={() => setMethod(m)}>
                  {METHOD_LABEL[m] ?? m}
                </button>
              ))}
            </div>
            <div className="label mt-3">face_weight（カンマ区切り）</div>
            <input className="input font-mono" value={weights.join(", ")} onChange={(e) => setWeights(e.target.value.split(",").map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x) && x > 0).slice(0, 5))} />
            <button className="btn-primary mt-3 w-full" onClick={run} disabled={!!job && !isFinished(job)} data-testid="run-verify">
              {job && !isFinished(job) ? "生成中…" : `検証を実行（${weights.length} × ${VERIFY_SCENES.length} 枚）`}
            </button>
          </div>
        </div>
        <button className="btn-secondary mt-3 w-full" onClick={onBack}>
          ← 参照顔を選び直す
        </button>
      </aside>
      <section className="space-y-3">
        <p className="text-xs text-slate-400">
          生成した顔が参照とどれくらい似ているかを ArcFace のコサイン類似度で測ります。低い画像は警告色（別人の可能性）。手法ごとにタブで比較し、採用する手法と weight を決めてください。
        </p>
        {job && !isFinished(job) && <JobRow job={job} />}
        {job?.status === "error" && <p className="text-sm text-rose-300">検証に失敗しました: {job.error}</p>}
        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="verify-grid">
              <thead>
                <tr>
                  <th className="p-1 text-left text-slate-400">シーン ＼ weight</th>
                  {weights.map((w) => (
                    <th key={w} className="p-1 text-center">
                      <div className="font-mono">{w}</div>
                      <div className="text-slate-400">平均 {byWeight[String(w)] !== undefined ? byWeight[String(w)]!.toFixed(2) : "—"}</div>
                      <button className="btn-primary mt-1 text-xs" onClick={() => onLock(method, w)} data-testid={`lock-weight-${w}`}>
                        この手法・この weight で固定
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {VERIFY_SCENES.map((scene) => (
                  <tr key={scene}>
                    <th className="p-1 text-left align-top text-slate-300">{sceneName(scene)}</th>
                    {weights.map((w) => {
                      const cell = items.find((it) => it.scene === scene && Math.abs(it.face_weight - w) < 1e-6);
                      return (
                        <td key={w} className="p-1 align-top">
                          {cell ? (
                            <div className="card overflow-hidden">
                              <BlurImage id={cell.image_id} src={`/api/images/${cell.image_id}/thumb`} alt={`${sceneName(scene)} weight ${w}`} className="aspect-[3/4]" />
                              <div className="p-1">
                                <SimilarityBadge value={cell.similarity} status={cell.similarity_status ?? null} />
                              </div>
                            </div>
                          ) : (
                            <div className="skeleton aspect-[3/4]" />
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!job && <p className="text-sm text-slate-500">「検証を実行」で {weights.length} × {VERIFY_SCENES.length} 枚を生成します。</p>}
      </section>
    </div>
  );
}

// ----------------------------------------------------------------------------- Step 4
function StepRegister({ draft, chosen, checkpoints, faceMethods, onBack, onRegistered }: { draft: CharacterDetail; chosen: { method: FaceMethod; weight: number } | null; checkpoints: string[]; faceMethods: string[]; onBack: () => void; onRegistered: (c: CharacterDetail) => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [tags, setTags] = useState("");
  const [description, setDescription] = useState("");
  const [isSynthetic, setIsSynthetic] = useState(false);
  const [adult, setAdult] = useState(false);
  const [locked, setLocked] = useState<LockedPatch>({
    ...(draft.locked ?? {}),
    face_method: chosen?.method ?? draft.locked?.face_method ?? "pulid",
    face_weight: chosen?.weight ?? draft.locked?.face_weight ?? 0.8,
  });
  const [busy, setBusy] = useState(false);
  const canRegister = name.trim().length > 0 && isSynthetic && adult && !busy;

  const register = async () => {
    setBusy(true);
    try {
      const c = unwrap(await api.POST("/api/characters/{character_id}/register", { params: { path: { character_id: draft.id } }, body: { name: name.trim(), tags: parseTags(tags), description, is_synthetic: isSynthetic, adult_confirmed: adult, locked } }));
      onRegistered(c);
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      {NOTICE}
      <div className="card space-y-3 p-4">
        <label className="block">
          <span className="label">名前（必須）</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} data-testid="register-name" />
        </label>
        <label className="block">
          <span className="label">タグ（カンマ区切り）</span>
          <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        <label className="block">
          <span className="label">説明</span>
          <textarea className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={isSynthetic} onChange={(e) => setIsSynthetic(e.target.checked)} data-testid="check-synthetic" className="mt-1" />
          <span>
            <strong>実在人物ではない</strong>ことを申告します（参照顔は架空のキャラクターです。実在の人物の顔・写真は使用していません）
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} data-testid="check-adult" className="mt-1" />
          <span>
            <strong>成人キャラクター</strong>であることを確認しました（未成年を思わせる表現は扱いません）
          </span>
        </label>
      </div>
      <details className="card p-4" open={false}>
        <summary className="cursor-pointer text-sm font-medium">▸ 固定設定（locked）を確認・微調整 — 既定値は Step 3 の結果</summary>
        <div className="mt-3">
          <LockedEditor value={locked} onChange={setLocked} checkpoints={checkpoints} faceMethods={faceMethods} />
        </div>
      </details>
      <div className="flex items-center justify-between">
        <button className="btn-secondary" onClick={onBack}>
          ← 戻る
        </button>
        <button className="btn-primary" disabled={!canRegister} onClick={register} data-testid="register-submit" title={!canRegister ? "名前と 2 つの申告が必要です" : ""}>
          登録
        </button>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------- Step 5
function StepDone({ character, onAnother }: { character: CharacterDetail; onAnother: () => void }) {
  const navigate = useNavigate();
  const primary = character.references.find((r) => r.is_primary) ?? character.references[0];
  return (
    <div className="mx-auto max-w-2xl space-y-4" data-testid="wizard-done">
      <div className="card flex gap-4 p-4">
        {primary && <img src={primary.file_url} alt="参照顔" className="h-48 w-36 rounded object-cover" />}
        <div className="text-sm">
          <h2 className="text-lg font-semibold">{character.name} を登録しました</h2>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-slate-400">版</dt>
            <dd>v{character.current_version}</dd>
            <dt className="text-slate-400">手法</dt>
            <dd>{character.locked ? METHOD_LABEL[character.locked.face_method ?? "pulid"] : "—"}</dd>
            <dt className="text-slate-400">face_weight</dt>
            <dd className="font-mono">{character.locked?.face_weight}</dd>
            <dt className="text-slate-400">checkpoint</dt>
            <dd className="font-mono">{character.locked?.checkpoint}</dd>
            <dt className="text-slate-400">解像度</dt>
            <dd className="font-mono">
              {character.locked?.default_width}×{character.locked?.default_height}
            </dd>
            <dt className="text-slate-400">参照顔</dt>
            <dd>{character.references.length} 枚</dd>
          </dl>
        </div>
      </div>
      <div className="flex gap-2">
        <button className="btn-primary" onClick={() => navigate(`/workspace/${character.id}`)} data-testid="go-workspace">
          このキャラで生成を始める →
        </button>
        <button className="btn-secondary" onClick={onAnother}>
          もう 1 体作る
        </button>
      </div>
    </div>
  );
}
