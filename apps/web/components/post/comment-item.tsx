"use client";

import Link from "next/link";
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { Avatar } from "@/components/ui/avatar";
import { cn } from "@/lib/cn";
import { formatRelativeTimeShort } from "@/lib/format";
import type { PostComment } from "@/lib/queries/comments";

export interface CommentItemProps {
  comment: PostComment;
  /** 表示名（キャラ: handle / 自分: display_name / 他人: user_xxxxxx） */
  label: string;
  /** 投稿者本人（キャラ）のコメントなら「作成者」を添える */
  isPostAuthor: boolean;
  isOwn: boolean;
  /** 返信（1 段インデント・小さいアバター） */
  isReply?: boolean;
  onReply: () => void;
  onDelete?: () => void;
}

/**
 * scrollIntoView で固定ヘッダー・固定フッター（コメント入力欄）の裏に隠れないようにする余白。
 * --composer-h は PostComments が入力欄の高さを設定する。
 */
export const COMMENT_SCROLL_MARGIN: CSSProperties = {
  scrollMarginTop: "calc(var(--header-h) + env(safe-area-inset-top) + 8px)",
  scrollMarginBottom: "calc(var(--composer-h, 0px) + env(safe-area-inset-bottom) + 12px)",
};

/** コメント 1 件（Instagram: アバター / 名前 + 時刻 / 本文 / 「返信する」） */
export function CommentItem({
  comment,
  label,
  isPostAuthor,
  isOwn,
  isReply = false,
  onReply,
  onDelete,
}: CommentItemProps) {
  const character = comment.author_type === "character" ? comment.character : null;
  const avatarSize = isReply ? "xs" : "sm";
  const profilePath = character ? `/c/${character.handle}` : null;

  const avatar = (
    <Avatar
      src={character?.avatar_url ?? null}
      alt={character ? character.name : label}
      size={avatarSize}
      // 他ユーザーは匿名のため頭文字ではなく人物アイコンを表示する
      fallbackText={character || isOwn ? undefined : ""}
    />
  );

  const linkTo = (children: ReactNode, className?: string, ariaLabel?: string) =>
    profilePath ? (
      <Link href={profilePath} className={className} aria-label={ariaLabel}>
        {children}
      </Link>
    ) : (
      <span className={className}>{children}</span>
    );

  return (
    <div
      id={`comment-${comment.id}`}
      className={cn("flex gap-3 py-2 pr-4", isReply ? "pl-[60px]" : "pl-4")}
      style={COMMENT_SCROLL_MARGIN}
      data-testid="comment"
      data-comment-id={comment.id}
      data-author-type={comment.author_type}
    >
      {linkTo(
        avatar,
        "pressable mt-0.5 shrink-0",
        character ? `${character.name}のプロフィール` : undefined,
      )}
      <div className="min-w-0 flex-1">
        <p className="text-[13px] leading-[18px]">
          {linkTo(label, "font-semibold")}
          {isPostAuthor ? <span className="text-ig-secondary"> • 作成者</span> : null}
          <time dateTime={comment.created_at} className="ml-2 text-[12px] text-ig-secondary">
            {formatRelativeTimeShort(comment.created_at)}
          </time>
        </p>
        <p className="text-[14px] leading-[18px] text-wrap-anywhere whitespace-pre-line">
          {comment.body}
        </p>
        <div className="mt-1 flex items-center gap-4 text-[12px] leading-4 font-semibold text-ig-secondary">
          <button type="button" onClick={onReply} className="pressable">
            返信する
          </button>
          {isOwn && onDelete ? (
            <button
              type="button"
              onClick={onDelete}
              className="pressable"
              aria-label="このコメントを削除"
            >
              削除
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** キャラが返信を書いている間の表示（reply_scheduled） */
export function CommentTypingRow({ name, avatarUrl }: { name: string; avatarUrl: string }) {
  const ref = useRef<HTMLDivElement>(null);
  // 表示されたら見える位置へ（入力欄の裏に隠れないように）
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);
  return (
    <div
      ref={ref}
      style={COMMENT_SCROLL_MARGIN}
      className="flex items-center gap-3 py-2 pr-4 pl-[60px]"
      role="status"
      aria-live="polite"
      data-testid="reply-typing"
    >
      <Avatar src={avatarUrl} alt={name} size="xs" />
      <p className="flex items-center gap-1.5 text-[12px] leading-4 text-ig-secondary">
        <span>
          {name}さんが返信を書いています<span className="sr-only">…</span>
        </span>
        <span aria-hidden="true" className="flex gap-0.5">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="size-1 animate-typing-dot rounded-full bg-ig-secondary"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </span>
      </p>
    </div>
  );
}
