"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { cn } from "@/lib/cn";

export interface SheetAction {
  label: string;
  onClick: () => void;
  destructive?: boolean;
}

/** Instagram の「…」メニュー形式のボトムシート（中央揃えの行 + 区切り線 + 最下段にキャンセル） */
export function ActionSheet({
  open,
  onClose,
  ariaLabel,
  actions,
  header,
}: {
  open: boolean;
  onClose: () => void;
  ariaLabel: string;
  actions: SheetAction[];
  header?: ReactNode;
}) {
  return (
    <BottomSheet open={open} onClose={onClose} ariaLabel={ariaLabel}>
      {header}
      <ul className="flex flex-col">
        {actions.map((action) => (
          <li key={action.label} className="border-ig-sheet-separator border-b">
            <button
              type="button"
              onClick={action.onClick}
              className={cn(
                "active:bg-ig-elevated flex h-[52px] w-full items-center justify-center px-4 text-[16px]",
                action.destructive && "text-ig-red font-semibold",
              )}
            >
              {action.label}
            </button>
          </li>
        ))}
        <li>
          <button
            type="button"
            onClick={onClose}
            className="active:bg-ig-elevated flex h-[52px] w-full items-center justify-center px-4 text-[16px]"
          >
            キャンセル
          </button>
        </li>
      </ul>
    </BottomSheet>
  );
}

/** 投稿ヘッダーの「…」: プロフィールを見る / リンクをコピー / キャンセル */
export function PostOptionsSheet({
  open,
  onClose,
  postId,
  handle,
  onCopyLink,
}: {
  open: boolean;
  onClose: () => void;
  postId: string;
  handle: string;
  onCopyLink: (path: string) => void;
}) {
  const router = useRouter();
  return (
    <ActionSheet
      open={open}
      onClose={onClose}
      ariaLabel="投稿のオプション"
      actions={[
        {
          label: "プロフィールを見る",
          onClick: () => {
            onClose();
            router.push(`/c/${handle}`);
          },
        },
        {
          label: "リンクをコピー",
          onClick: () => {
            onClose();
            onCopyLink(`/posts/${postId}`);
          },
        },
      ]}
    />
  );
}
