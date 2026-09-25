import { ListRowSkeleton, Skeleton } from "@/components/ui/skeleton";

/** DM 一覧の読み込み中 */
export default function DmListLoading() {
  return (
    <div aria-busy="true" aria-label="読み込み中">
      <div className="sticky top-0 z-30 bg-ig-bg pt-safe">
        <div className="flex h-[var(--header-h)] items-center px-4">
          <Skeleton shape="text" className="h-5 w-28" />
        </div>
      </div>
      <div className="px-4 pt-1 pb-3">
        <Skeleton className="h-9 w-full rounded-[10px]" />
      </div>
      {Array.from({ length: 6 }, (_, i) => (
        <ListRowSkeleton key={i} />
      ))}
    </div>
  );
}
