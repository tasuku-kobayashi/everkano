import { useEffect, useRef } from "react";
import clsx from "clsx";
import { api, getErrorMessage, type Job } from "../api/client";
import { isFinished, useInvalidateAfterJob, useJobs } from "../api/queries";
import { useToast } from "./Toast";

const TYPE_LABEL: Record<Job["type"], string> = { generate: "生成", draft: "種顔", verify: "検証" };

/** Persistent bottom bar: running / queued jobs with progress, remaining count and cancel. Polls every second. */
export function JobBar() {
  const { data: jobs } = useJobs();
  const toast = useToast();
  const invalidate = useInvalidateAfterJob();
  const seen = useRef<Map<string, Job["status"]>>(new Map());
  const initialized = useRef(false);

  useEffect(() => {
    if (!jobs) return;
    for (const job of jobs) {
      const previous = seen.current.get(job.id);
      seen.current.set(job.id, job.status);
      // A job that finished between two polls is first seen already finished: treat it as a transition too
      // (but not the jobs that were already finished when the page loaded).
      const transitioned = previous ? previous !== job.status : initialized.current;
      if (transitioned && isFinished(job)) {
        invalidate();
        if (job.status === "done") toast.success(`${TYPE_LABEL[job.type]}ジョブが完了しました（${job.result_image_ids.length} 枚）`);
        if (job.status === "error") toast.error(`${TYPE_LABEL[job.type]}ジョブが失敗しました: ${job.error ?? "不明なエラー"}`);
        if (job.status === "canceled") toast.info("ジョブをキャンセルしました");
      }
    }
    initialized.current = true;
  }, [jobs, invalidate, toast]);

  const active = (jobs ?? []).filter((j) => j.status === "running" || j.status === "queued");
  if (!active.length) return null;
  const running = active.find((j) => j.status === "running");
  const queued = active.filter((j) => j.status === "queued");

  const cancel = async (id: string) => {
    try {
      const r = await api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: id } } });
      if (r.error) throw new Error(getErrorMessage(r.error));
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  return (
    <div className="border-t border-ink-700 bg-ink-900 px-4 py-2 text-sm" data-testid="job-bar">
      {running ? <JobRow job={running} onCancel={() => cancel(running.id)} /> : <div className="text-slate-400">待機中のジョブ {queued.length} 件</div>}
      {queued.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-400">
          {queued.map((j, i) => (
            <span key={j.id} className="rounded bg-ink-800 px-2 py-0.5">
              #{i + 1} {TYPE_LABEL[j.type]} {j.progress.total_images} 枚
              <button className="ml-2 text-rose-300 hover:underline" onClick={() => cancel(j.id)}>
                取消
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function JobRow({ job, onCancel, compact }: { job: Job; onCancel?: () => void; compact?: boolean }) {
  const p = job.progress;
  const stepPct = p.total ? Math.round((p.step / p.total) * 100) : 0;
  const imagePct = p.total_images ? Math.round((((p.current - 1) + (p.total ? p.step / p.total : 0)) / p.total_images) * 100) : 0;
  const remaining = Math.max(0, p.total_images - Math.max(0, p.current - 1));
  return (
    <div className={clsx("flex items-center gap-3", compact && "text-xs")} data-testid="job-row" data-status={job.status}>
      <span className="shrink-0 font-medium">
        {TYPE_LABEL[job.type]} {job.status === "queued" ? "（待機中）" : ""}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex justify-between text-xs text-slate-400">
          <span className="truncate">{p.stage || "…"}</span>
          <span className="font-mono">
            {p.current}/{p.total_images} 枚 · step {p.step}/{p.total || "?"} · 残り {remaining} 枚
          </span>
        </div>
        <div className="mt-0.5 h-2 w-full rounded bg-ink-700">
          <div className="h-2 rounded bg-accent transition-[width]" style={{ width: `${Math.max(imagePct, job.status === "running" ? 2 : 0)}%` }} />
        </div>
        {!compact && <div className="text-[10px] text-slate-500">現在の画像 {stepPct}%</div>}
      </div>
      {onCancel && (job.status === "running" || job.status === "queued") && (
        <button className="btn-secondary shrink-0" onClick={onCancel} data-testid="cancel-job">
          キャンセル
        </button>
      )}
    </div>
  );
}
