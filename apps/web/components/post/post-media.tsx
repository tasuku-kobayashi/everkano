"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { HeartFilledIcon } from "@/components/ui/icons";
import { CdnImage } from "@/components/ui/image";
import type { Post } from "@/lib/queries/posts";
import { PaidImage } from "./paid-image";

/** この時間内の 2 回目のタップをダブルタップとみなす（ms） */
export const DOUBLE_TAP_MS = 250;

export interface PostMediaProps {
  post: Post;
  /** ダブルタップ（いいね） */
  onDoubleTap: () => void;
  /** シングルタップ（無料: 投稿詳細へ / 有料: ロックモーダル）。未指定なら何もしない */
  onSingleTap?: () => void;
  /** シングルタップの説明（スクリーンリーダー向け） */
  singleTapLabel?: string;
  priority?: boolean;
}

/**
 * 投稿画像（正方形 1:1）。
 * - ダブルタップでいいね + 大きな白いハートのアニメーション（Instagram と同じ）
 * - シングルタップはダブルタップ判定の猶予（250ms）後に実行する
 * - 有料投稿は全面ぼかし + 鍵 + 「有料コンテンツ」バッジ
 */
export function PostMedia({
  post,
  onDoubleTap,
  onSingleTap,
  singleTapLabel,
  priority,
}: PostMediaProps) {
  const lastTapAt = useRef(0);
  const singleTapTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [burst, setBurst] = useState(0);

  useEffect(() => () => clearTimeout(singleTapTimer.current), []);

  const handleTap = () => {
    const now = Date.now();
    if (now - lastTapAt.current < DOUBLE_TAP_MS) {
      clearTimeout(singleTapTimer.current);
      lastTapAt.current = 0;
      setBurst((n) => n + 1);
      onDoubleTap();
      return;
    }
    lastTapAt.current = now;
    if (onSingleTap) {
      clearTimeout(singleTapTimer.current);
      singleTapTimer.current = setTimeout(() => {
        lastTapAt.current = 0;
        onSingleTap();
      }, DOUBLE_TAP_MS);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onSingleTap) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSingleTap();
    }
  };

  const alt = post.caption
    ? `${post.character.name}の投稿: ${post.caption.slice(0, 60)}`
    : `${post.character.name}の投稿`;

  return (
    <div
      className="relative touch-manipulation select-none"
      onClick={handleTap}
      onKeyDown={handleKeyDown}
      role={onSingleTap ? "button" : undefined}
      tabIndex={onSingleTap ? 0 : undefined}
      aria-label={onSingleTap ? singleTapLabel : undefined}
      data-testid="post-media"
    >
      {post.is_paid ? (
        <PaidImage
          src={post.image_url}
          alt={alt}
          priceTokens={post.price_tokens}
          variant="full"
          priority={priority}
        />
      ) : (
        <CdnImage
          src={post.image_url}
          alt={alt}
          className="aspect-square w-full"
          widths={[640, 1080]}
          priority={priority}
        />
      )}
      {burst > 0 ? (
        <span
          key={burst}
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
        >
          <HeartFilledIcon
            size={104}
            strokeWidth={0}
            className="animate-heart-burst text-white drop-shadow-[0_2px_12px_rgb(0_0_0/0.35)]"
            onAnimationEnd={() => setBurst(0)}
          />
        </span>
      ) : null}
    </div>
  );
}
