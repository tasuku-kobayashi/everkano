import { EmptyState } from "./empty-state";
import { GridIcon } from "./icons";

/** 未実装画面のプレースホルダー（機能実装時に置き換える） */
export function ComingSoon({ title = "準備中" }: { title?: string }) {
  return (
    <EmptyState
      icon={<GridIcon size={30} strokeWidth={1.6} />}
      title={title}
      description="この画面は現在準備中です。"
    />
  );
}
