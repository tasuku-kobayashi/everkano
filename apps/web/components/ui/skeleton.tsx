import type { CSSProperties } from "react";
import { cn } from "@/lib/cn";

export interface SkeletonProps {
  className?: string;
  /** rect: 角丸 4px / circle: 円 / text: 1 行分の高さ */
  shape?: "rect" | "circle" | "text";
  style?: CSSProperties;
}

/** className で高さを指定しているか（h-* / size-*） */
function hasHeightClass(className: string | undefined): boolean {
  return className !== undefined && /(?:^|\s)(?:h|size)-/.test(className);
}

/**
 * 読み込み中のプレースホルダー（シマー付き）。サイズは className（w-*, h-*）で指定。
 * shape="text" の既定の高さ（h-3）は、className で高さを指定していないときだけ付ける
 * （cn は同じ種類のクラスの衝突を解決しないため、両方を付けると CSS の順序で h-3 が勝つことがある）。
 */
export function Skeleton({ className, shape = "rect", style }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      style={style}
      className={cn(
        "block skeleton",
        shape === "circle" && "rounded-full",
        shape === "rect" && "rounded",
        shape === "text" && "rounded-full",
        shape === "text" && !hasHeightClass(className) && "h-3",
        className,
      )}
    />
  );
}

/** フィードの投稿カード 1 件分のスケルトン（ホーム・(main)/loading.tsx で使用） */
export function PostCardSkeleton() {
  return (
    <div className="pb-4" aria-hidden="true">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <Skeleton shape="circle" className="size-8" />
        <Skeleton shape="text" className="w-28" />
      </div>
      <Skeleton className="aspect-square w-full rounded-none" />
      <div className="space-y-2 px-3 pt-3">
        <Skeleton shape="text" className="w-24" />
        <Skeleton shape="text" className="w-4/5" />
        <Skeleton shape="text" className="w-3/5" />
      </div>
    </div>
  );
}

/** リスト 1 行分（アバター + 2 行テキスト）のスケルトン（DM 一覧・検索結果など） */
export function ListRowSkeleton() {
  return (
    <div className="flex items-center gap-3 px-4 py-2" aria-hidden="true">
      <Skeleton shape="circle" className="size-14 shrink-0" />
      <div className="flex-1 space-y-2">
        <Skeleton shape="text" className="w-32" />
        <Skeleton shape="text" className="w-48" />
      </div>
    </div>
  );
}
