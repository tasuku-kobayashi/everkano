"use client";

import Link from "next/link";
import { useState } from "react";
import { CdnImage } from "@/components/ui/image";
import { Skeleton } from "@/components/ui/skeleton";
import type { Post } from "@/lib/queries/posts";
import { PaidImage } from "./paid-image";
import { PaidLockModal } from "./paid-lock-modal";

/**
 * 3 列の正方形グリッド（プロフィールの無料/有料タブ・検索の発見タブ）。
 * 無料投稿はタップで投稿詳細へ、有料投稿は全面ぼかし + 鍵 + 価格で、タップするとロックモーダル（遷移しない）。
 */
export function PostGrid({ posts }: { posts: readonly Post[] }) {
  // 閉じるアニメーション中も価格を表示し続けるため、対象の投稿と開閉を別々に持つ
  const [locked, setLocked] = useState<Post | null>(null);
  const [lockOpen, setLockOpen] = useState(false);
  return (
    <>
      <ul className="grid grid-cols-3 gap-0.5" data-testid="post-grid">
        {posts.map((post) => (
          <li key={post.id}>
            {post.is_paid ? (
              <button
                type="button"
                onClick={() => {
                  setLocked(post);
                  setLockOpen(true);
                }}
                aria-label={`有料コンテンツ（${post.price_tokens} tokens）`}
                className="block w-full active:opacity-80"
                data-testid="grid-paid-tile"
              >
                <PaidImage
                  src={post.image_url}
                  alt={`${post.character.name}の有料投稿`}
                  priceTokens={post.price_tokens}
                  variant="tile"
                />
              </button>
            ) : (
              <Link
                href={`/posts/${post.id}`}
                aria-label={`${post.character.name}の投稿を開く`}
                className="block active:opacity-80"
                data-testid="grid-free-tile"
              >
                <CdnImage
                  src={post.image_url}
                  alt={post.caption ? post.caption.slice(0, 40) : `${post.character.name}の投稿`}
                  className="aspect-square w-full"
                  widths={[320]}
                  sizes="(max-width: 480px) 33vw, 160px"
                />
              </Link>
            )}
          </li>
        ))}
      </ul>
      <PaidLockModal
        open={lockOpen}
        onClose={() => setLockOpen(false)}
        priceTokens={locked?.price_tokens ?? 0}
        characterName={locked?.character.name}
      />
    </>
  );
}

/** グリッドのスケルトン（3 列 × rows 行） */
export function PostGridSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid grid-cols-3 gap-0.5" aria-hidden="true">
      {Array.from({ length: rows * 3 }, (_, i) => (
        <Skeleton key={i} className="aspect-square w-full rounded-none" />
      ))}
    </div>
  );
}
