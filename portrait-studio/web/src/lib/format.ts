export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "未使用";
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.round(diff / 60_000);
  if (min < 1) return "たった今";
  if (min < 60) return `${min} 分前`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} 時間前`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} 日前`;
  return formatDate(iso).slice(0, 10);
}

export function formatMb(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return "—";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

export function pct(value: number, total: number): number {
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round((value / total) * 100)));
}

export function similarityLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? "顔検出不可" : value.toFixed(2);
}

export type SimilarityGrade = "good" | "acceptable" | "warning" | "unknown";

export function gradeSimilarity(value: number | null | undefined, t: { good: number; acceptable: number }): SimilarityGrade {
  if (value === null || value === undefined) return "unknown";
  if (value >= t.good) return "good";
  if (value >= t.acceptable) return "acceptable";
  return "warning";
}

export const GRADE_CLASS: Record<SimilarityGrade, string> = {
  good: "bg-emerald-900/70 text-emerald-200 border border-emerald-700",
  acceptable: "bg-amber-900/70 text-amber-200 border border-amber-700",
  warning: "bg-rose-900/80 text-rose-100 border border-rose-600",
  unknown: "bg-ink-700 text-slate-300 border border-ink-600",
};

export const GRADE_TEXT: Record<SimilarityGrade, string> = {
  good: "良好",
  acceptable: "許容",
  warning: "別人の可能性",
  unknown: "顔検出不可",
};

export const WARNING_TEXT: Record<string, string> = {
  no_face: "顔が検出できません（使用不可）",
  multiple_faces: "複数人が写っています",
  face_too_small: "顔が小さい",
  low_det_score: "検出信頼度が低い",
  not_frontal: "横顔・傾き",
  blurry: "ぼけ",
  pose_unavailable: "向きを推定できず",
};

export const METHOD_LABEL: Record<string, string> = {
  pulid: "PuLID",
  faceid: "IP-Adapter FaceID Plus v2",
  instantid: "InstantID",
};

export const RESOLUTION_STEPS: Array<[number, number]> = [
  [640, 960],
  [704, 1024],
  [768, 1152],
  [832, 1216],
  [896, 1344],
  [1024, 1536],
];

export function lowerResolution(width: number, height: number): [number, number] {
  const idx = RESOLUTION_STEPS.findIndex(([w, h]) => w === width && h === height);
  if (idx > 0) return RESOLUTION_STEPS[idx - 1]!;
  const w = Math.max(512, Math.round((width * 0.9) / 8) * 8);
  const h = Math.max(512, Math.round((height * 0.9) / 8) * 8);
  return [w, h];
}

export function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const el = document.createElement("textarea");
  el.value = text;
  document.body.appendChild(el);
  el.select();
  document.execCommand("copy");
  document.body.removeChild(el);
  return Promise.resolve();
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Local calendar day (YYYY-MM-DD from <input type="date">) -> UTC ISO bounds. The API compares ISO strings in UTC. */
export function dayStartIso(localDate: string): string {
  return new Date(`${localDate}T00:00:00`).toISOString();
}

export function dayEndIso(localDate: string): string {
  return new Date(`${localDate}T23:59:59.999`).toISOString();
}

/** Inverse of the two above for the input's `value`: the local calendar day of an ISO timestamp. */
export function isoToLocalDateInput(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
