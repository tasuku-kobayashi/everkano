import { PostGridSkeleton } from "@/components/post/post-grid";
import { SearchIcon } from "@/components/ui/icons";

export default function SearchLoading() {
  return (
    <div aria-busy="true" aria-label="読み込み中">
      <div className="sticky top-0 z-30 bg-ig-bg pt-safe">
        <div className="flex h-[52px] items-center px-4">
          <div className="flex h-9 flex-1 items-center gap-2 rounded-[10px] bg-ig-elevated px-3 text-[16px] text-ig-secondary">
            <SearchIcon size={16} strokeWidth={2.4} />
            検索
          </div>
        </div>
      </div>
      <PostGridSkeleton rows={5} />
    </div>
  );
}
