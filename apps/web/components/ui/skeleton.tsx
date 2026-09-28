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

/**
 * フィードの投稿カード 1 件分のスケルトン（ホーム・(main)/loading.tsx で使用）。
 * 高さを本物のカード（components/post/post-card.tsx: ヘッダー 54px・正方形の画像・操作の行 46px・
 * いいね数・キャプション 2 行・時刻）に合わせ、読み込み後に画面が飛ばないようにする
 */
export function PostCardSkeleton() {
  return (
    <div className="pb-3" aria-hidden="true">
      <div className="flex h-[54px] items-center gap-2.5 pl-3">
        <Skeleton shape="circle" className="size-8" />
        <Skeleton shape="text" className="w-28" />
      </div>
      <Skeleton className="aspect-square w-full rounded-none" />
      <div className="flex h-[46px] items-center gap-4 px-3">
        <Skeleton shape="circle" className="size-6" />
        <Skeleton shape="circle" className="size-6" />
        <Skeleton shape="circle" className="size-6" />
      </div>
      <div className="space-y-2 px-3">
        <Skeleton shape="text" className="w-24" />
        <Skeleton shape="text" className="w-4/5" />
        <Skeleton shape="text" className="w-3/5" />
        <Skeleton shape="text" className="h-2.5 w-12" />
      </div>
    </div>
  );
}

/**
 * ホーム上部のストーリーズ行のスケルトン（components/feed/stories-row.tsx の StorySkeleton と同じ寸法:
 * 幅 76px・アバター 73px（62px + リング）・ハンドル 1 行 16px）。ルートの読み込み中（(main)/loading.tsx）に使う
 */
export function StoriesRowSkeleton() {
  return (
    <div className="border-b border-ig-separator" aria-hidden="true">
      <div className="scrollbar-none flex gap-2.5 overflow-x-hidden px-2.5 pt-2.5 pb-2">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex w-[76px] shrink-0 flex-col items-center gap-1.5">
            <Skeleton shape="circle" className="size-[73px]" />
            <span className="flex h-4 w-full items-center justify-center">
              <Skeleton shape="text" className="w-12" />
            </span>
          </div>
        ))}
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
