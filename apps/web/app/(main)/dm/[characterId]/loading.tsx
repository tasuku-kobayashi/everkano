import { Skeleton } from "@/components/ui/skeleton";

/** DM 会話の読み込み中（ヘッダー + 吹き出しのスケルトン） */
export default function DmConversationLoading() {
  const bubbles = [
    { own: false, width: "w-44" },
    { own: true, width: "w-36" },
    { own: false, width: "w-56" },
    { own: true, width: "w-28" },
  ];
  return (
    <div className="flex flex-1 flex-col" aria-busy="true" aria-label="読み込み中">
      <div className="sticky top-0 z-30 border-b border-ig-separator bg-ig-bg pt-safe">
        <div className="flex h-[var(--header-h)] items-center gap-2.5 px-3">
          <span className="size-10" />
          <Skeleton shape="circle" className="size-7" />
          <Skeleton shape="text" className="w-24" />
        </div>
      </div>
      <div className="flex flex-1 flex-col justify-end space-y-3 px-3 pb-[76px]">
        {bubbles.map((bubble) => (
          <div
            key={bubble.width}
            className={bubble.own ? "flex justify-end" : "flex items-end gap-2"}
          >
            {bubble.own ? null : <Skeleton shape="circle" className="size-7" />}
            <Skeleton className={`h-9 rounded-[22px] ${bubble.width}`} />
          </div>
        ))}
      </div>
    </div>
  );
}
