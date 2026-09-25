import { PostCardSkeleton, Skeleton } from "@/components/ui/skeleton";

/**
 * (main) 配下の画面遷移中に表示するスケルトン（各機能は自分のルートに loading.tsx を置いて上書きできる）。
 * data-route-loading（ROUTE_LOADING_ATTRIBUTE）: 表示中はホームのスクロール位置を復元しない（lib/home-scroll.ts）。
 */
export default function MainLoading() {
  return (
    <div aria-busy="true" aria-label="読み込み中" data-route-loading="">
      <div className="sticky top-0 z-30 bg-ig-bg pt-safe">
        <div className="flex h-[var(--header-h)] items-center px-4">
          <Skeleton shape="text" className="h-4 w-28" />
        </div>
      </div>
      <PostCardSkeleton />
      <PostCardSkeleton />
    </div>
  );
}
