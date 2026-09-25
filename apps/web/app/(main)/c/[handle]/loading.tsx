import { PostGridSkeleton } from "@/components/post/post-grid";
import { Skeleton } from "@/components/ui/skeleton";

export default function CharacterLoading() {
  return (
    <div aria-busy="true" aria-label="読み込み中">
      <div className="pt-safe bg-ig-bg sticky top-0 z-30">
        <div className="flex h-[var(--header-h)] items-center justify-center px-4">
          <Skeleton shape="text" className="h-4 w-28" />
        </div>
      </div>
      <div className="flex items-center gap-4 px-4 pt-3" aria-hidden="true">
        <Skeleton shape="circle" className="size-[86px] shrink-0" />
        <div className="flex flex-1 justify-around">
          <Skeleton shape="text" className="h-8 w-12" />
          <Skeleton shape="text" className="h-8 w-14" />
        </div>
      </div>
      <div className="space-y-2 px-4 pt-4 pb-4" aria-hidden="true">
        <Skeleton shape="text" className="w-24" />
        <Skeleton shape="text" className="w-4/5" />
        <Skeleton className="mt-3 h-8 w-full rounded-lg" />
      </div>
      <PostGridSkeleton rows={2} />
    </div>
  );
}
