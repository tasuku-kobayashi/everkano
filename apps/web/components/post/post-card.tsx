"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Avatar } from "@/components/ui/avatar";
import {
  BookmarkIcon,
  CommentIcon,
  HeartFilledIcon,
  HeartIcon,
  MoreIcon,
  ShareIcon,
} from "@/components/ui/icons";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/cn";
import { formatCount, formatRelativeTime } from "@/lib/format";
import { usePostLike } from "@/lib/queries/likes";
import type { Post } from "@/lib/queries/posts";
import { PaidLockModal } from "./paid-lock-modal";
import { PostCaption } from "./post-caption";
import { PostMedia } from "./post-media";
import { PostOptionsSheet } from "./post-options-sheet";
import { useCopyLink, useShareLink } from "./use-share";

export interface PostCardProps {
  post: Post;
  /** feed: キャプション 2 行 + 「コメントN件をすべて見る」 / detail: キャプション全文 */
  variant?: "feed" | "detail";
  /** ファーストビューの画像（lazy を無効化） */
  priority?: boolean;
  /** 投稿詳細でコメントアイコンを押したとき（入力欄にフォーカス） */
  onCommentClick?: () => void;
}

/**
 * いいね数（Instagram と同じく投稿の下では省略せず桁区切りで表示する。例: 「いいね！」11,801件）。
 * 1 件の増減が目に見えるように、万単位の短縮表記（formatCount）は使わない。
 */
export function formatExactLikeCount(value: number): string {
  const n = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  return `「いいね！」${n.toLocaleString("ja-JP")}件`;
}

/** Instagram 準拠の投稿カード（仕様 §4.3） */
export function PostCard({ post, variant = "feed", priority, onCommentClick }: PostCardProps) {
  const router = useRouter();
  const toast = useToast();
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [lockOpen, setLockOpen] = useState(false);
  const [popKey, setPopKey] = useState(0);
  const copyLink = useCopyLink();
  const share = useShareLink();
  const like = usePostLike(post.id, {
    onError: () => toast.error("「いいね！」できませんでした。通信状況を確認してください"),
  });

  const postPath = `/posts/${post.id}`;
  const profilePath = `/c/${post.character.handle}`;
  const isFeed = variant === "feed";

  const toggleLike = () => {
    const next = !post.liked;
    if (next) setPopKey((n) => n + 1);
    like.mutate(next);
  };

  const likeByDoubleTap = () => {
    if (!post.liked) {
      setPopKey((n) => n + 1);
      like.mutate(true);
    }
  };

  const singleTap = post.is_paid
    ? () => setLockOpen(true)
    : isFeed
      ? () => router.push(postPath)
      : undefined;

  return (
    <article
      className="pb-3"
      data-testid="post-card"
      data-post-id={post.id}
      data-paid={post.is_paid ? "true" : "false"}
    >
      {/* ヘッダー: アバター + handle + 「…」 */}
      <header className="flex h-[54px] items-center gap-2.5 pr-1 pl-3">
        <Link
          href={profilePath}
          aria-label={`${post.character.name}のプロフィール`}
          className="shrink-0 pressable"
        >
          <Avatar src={post.character.avatar_url} alt={post.character.name} size="sm" />
        </Link>
        <div className="min-w-0 flex-1">
          <Link
            href={profilePath}
            className="block truncate text-[14px] leading-[18px] font-semibold"
          >
            {post.character.handle}
          </Link>
        </div>
        <button
          type="button"
          onClick={() => setOptionsOpen(true)}
          aria-label="その他のオプション"
          className="flex size-10 shrink-0 items-center justify-center pressable"
        >
          <MoreIcon size={24} />
        </button>
      </header>

      <PostMedia
        post={post}
        priority={priority}
        onDoubleTap={likeByDoubleTap}
        onSingleTap={singleTap}
        singleTapLabel={post.is_paid ? "有料コンテンツの詳細を見る" : "投稿を開く"}
      />

      {/* アクション: いいね / コメント / シェア（左）・保存（右） */}
      <div className="flex h-[46px] items-center px-1.5">
        <button
          type="button"
          onClick={toggleLike}
          aria-label={post.liked ? "「いいね！」を取り消す" : "「いいね！」する"}
          aria-pressed={post.liked}
          className="flex size-10 items-center justify-center pressable"
          data-testid="like-button"
        >
          {post.liked ? (
            <HeartFilledIcon
              key={popKey}
              size={26}
              className={cn("text-ig-red", popKey > 0 && "animate-like-pop")}
            />
          ) : (
            <HeartIcon size={26} />
          )}
        </button>
        {isFeed ? (
          <Link
            href={postPath}
            aria-label="コメントを見る"
            className="flex size-10 items-center justify-center pressable"
          >
            <CommentIcon size={25} />
          </Link>
        ) : (
          <button
            type="button"
            onClick={onCommentClick}
            aria-label="コメントする"
            className="flex size-10 items-center justify-center pressable"
          >
            <CommentIcon size={25} />
          </button>
        )}
        <button
          type="button"
          onClick={() => void share(postPath, `${post.character.name}の投稿`)}
          aria-label="シェア"
          className="flex size-10 items-center justify-center pressable"
        >
          <ShareIcon size={24} />
        </button>
        <button
          type="button"
          onClick={() => toast.show("保存機能は現在準備中です")}
          aria-label="保存（準備中）"
          className="ml-auto flex size-10 items-center justify-center pressable"
        >
          <BookmarkIcon size={24} />
        </button>
      </div>

      <p className="px-3 text-[14px] leading-[18px] font-semibold" data-testid="like-count">
        {formatExactLikeCount(post.like_count)}
      </p>

      {post.caption ? (
        <div className="mt-1">
          <PostCaption handle={post.character.handle} caption={post.caption} clamp={isFeed} />
        </div>
      ) : null}

      {isFeed && post.comment_count > 0 ? (
        <Link
          href={postPath}
          className="mt-1 block px-3 text-[14px] leading-[18px] text-ig-secondary"
        >
          コメント{formatCount(post.comment_count)}件をすべて見る
        </Link>
      ) : null}

      <time
        dateTime={post.published_at}
        className="mt-1 block px-3 text-[12px] leading-4 text-ig-secondary"
      >
        {formatRelativeTime(post.published_at)}
      </time>

      <PostOptionsSheet
        open={optionsOpen}
        onClose={() => setOptionsOpen(false)}
        postId={post.id}
        handle={post.character.handle}
        onCopyLink={(path) => void copyLink(path)}
      />
      {post.is_paid ? (
        <PaidLockModal
          open={lockOpen}
          onClose={() => setLockOpen(false)}
          priceTokens={post.price_tokens}
          characterName={post.character.name}
        />
      ) : null}
    </article>
  );
}
