import clsx from "clsx";
import type { Quality } from "../api/client";
import { GRADE_CLASS, GRADE_TEXT, gradeSimilarity, similarityLabel, WARNING_TEXT } from "../lib/format";
import { useUiStore } from "../store/ui";

export function SimilarityBadge({ value, status, compact }: { value: number | null | undefined; status?: string | null; compact?: boolean }) {
  const thresholds = useUiStore((s) => s.similarity);
  const grade = gradeSimilarity(value, thresholds);
  const text = status === "no_reference" ? "参照なし" : status === "error" ? "分析エラー" : similarityLabel(value);
  return (
    <span className={clsx("badge font-mono", GRADE_CLASS[grade])} title={`ArcFace コサイン類似度: ${GRADE_TEXT[grade]}`} data-grade={grade}>
      {compact ? text : `類似度 ${text}`}
      {!compact && grade === "warning" && <span className="ml-1 font-sans">別人の可能性</span>}
    </span>
  );
}

export function QualityBadge({ quality }: { quality: Quality | null | undefined }) {
  if (!quality) return <span className="badge bg-rose-900 text-rose-100">使用不可</span>;
  const cls =
    quality.grade === "recommended"
      ? "bg-emerald-900/70 text-emerald-200 border border-emerald-700"
      : quality.grade === "acceptable"
        ? "bg-amber-900/70 text-amber-200 border border-amber-700"
        : "bg-rose-900/80 text-rose-100 border border-rose-600";
  const label = quality.grade === "recommended" ? "推奨" : quality.grade === "acceptable" ? "許容" : "非推奨";
  return (
    <span className={clsx("badge font-mono", cls)} title="品質スコア（composite）">
      {quality.composite.toFixed(2)} <span className="ml-1 font-sans">{label}</span>
    </span>
  );
}

export function WarningChips({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {warnings.map((w) => (
        <span key={w} className="badge border border-rose-700 bg-rose-950 text-rose-200">
          {WARNING_TEXT[w] ?? w}
        </span>
      ))}
    </div>
  );
}

export function Meter({ label, value, max, format, warn }: { label: string; value: number; max: number; format?: (v: number) => string; warn?: boolean }) {
  const ratio = Math.max(0, Math.min(1, max ? value / max : 0));
  return (
    <div className="text-[11px]">
      <div className="flex justify-between text-slate-400">
        <span>{label}</span>
        <span className={clsx("font-mono", warn && "text-rose-300")}>{format ? format(value) : value}</span>
      </div>
      <div className="mt-0.5 h-1.5 rounded bg-ink-700">
        <div className={clsx("h-1.5 rounded", warn ? "bg-rose-500" : "bg-accent")} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}
