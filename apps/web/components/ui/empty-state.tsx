import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface EmptyStateProps {
  /** 丸枠の中に表示するアイコン（例: <GridIcon size={34} />） */
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** ボタン等 */
  action?: ReactNode;
  className?: string;
}

/** Instagram の「投稿はまだありません」形式の空状態（丸枠アイコン + 太字見出し + 補足） */
export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center px-8 py-14 text-center", className)}>
      {icon ? (
        <div className="border-ig-text text-ig-text mb-4 flex size-[62px] items-center justify-center rounded-full border-2">
          {icon}
        </div>
      ) : null}
      <h2 className="text-[22px] leading-7 font-extrabold">{title}</h2>
      {description ? (
        <p className="text-ig-secondary mt-2 max-w-[320px] text-[14px] leading-[18px] text-balance">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
