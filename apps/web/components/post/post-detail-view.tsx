"use client";

import { useCallback, useRef } from "react";
import { AppHeader } from "@/components/ui/app-header";
import { ErrorState } from "@/components/ui/error-state";
import { PostCardSkeleton } from "@/components/ui/skeleton";
import { usePrefetchComments } from "@/lib/queries/comments";
import { usePost } from "@/lib/queries/posts";
import { OPAQUE_HEADER } from "./opaque-header";
import { PageUnavailable } from "./page-unavailable";
import { PostCard } from "./post-card";
import { PostComments } from "./post-comments";

/** 投稿詳細（/posts/[postId]）: 投稿カード（キャプション全文）+ コメント一覧 + 固定フッターの入力欄 */
export function PostDetailView({ postId }: { postId: string }) {
  const { data: post, isPending, isError, refetch, isRefetching } = usePost(postId);
  // コメントは投稿 ID だけで取れるので、投稿本体の取得と並行して取りに行く（直列の待ちをなくす）
  usePrefetchComments(postId);
  const inputRef = useRef<HTMLInputElement>(null);
  const focusCommentInput = useCallback(() => inputRef.current?.focus(), []);

  return (
    <>
      <PostDetailHeader handle={post?.character.handle} />
      {isPending ? (
        <PostDetailSkeleton />
      ) : isError && !post ? (
        <ErrorState onRetry={() => void refetch()} retrying={isRefetching} />
      ) : !post ? (
        <PageUnavailable />
      ) : (
        <>
          <PostCard post={post} variant="detail" priority onCommentClick={focusCommentInput} />
          <PostComments post={post} inputRef={inputRef} />
        </>
      )}
    </>
  );
}

/** Instagram の投稿画面のヘッダー（上に小さく handle、下に太字で「投稿」） */
export function PostDetailHeader({ handle }: { handle?: string }) {
  return (
    <AppHeader
      variant="back"
      bordered
      className={OPAQUE_HEADER}
      title={
        <span className="flex flex-col items-center">
          {handle ? (
            <span className="max-w-full truncate text-[12px] leading-4 font-semibold text-ig-secondary uppercase">
              {handle}
            </span>
          ) : null}
          <span>投稿</span>
        </span>
      }
    />
  );
}

export function PostDetailSkeleton() {
  return (
    <div aria-busy="true" aria-label="読み込み中">
      <PostCardSkeleton />
    </div>
  );
}
