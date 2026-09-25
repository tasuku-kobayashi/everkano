"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import { ErrorState } from "@/components/ui/error-state";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { getErrorMessage, isApiError } from "@/lib/api";
import { accountDisplayName, useMyAccount } from "@/lib/auth/account";
import {
  buildCommentThreads,
  commentAuthorLabel,
  isOwnComment,
  useComments,
  useCommentsRealtime,
  useCreateComment,
  useDeleteComment,
  type PostComment,
} from "@/lib/queries/comments";
import { queryKeys } from "@/lib/queries/keys";
import type { Post } from "@/lib/queries/posts";
import { CommentComposer } from "./comment-composer";
import { CommentItem, CommentTypingRow } from "./comment-item";

/** キャラの自動返信を待つ最大時間（これを過ぎたら「返信を書いています…」を消す） */
export const REPLY_WAIT_TIMEOUT_MS = 30_000;

interface PendingReply {
  /** 自分が投稿したコメント（キャラの返信の parent_comment_id） */
  commentId: string;
  /** 表示するスレッド（トップレベルのコメント） */
  rootId: string;
}

interface ReplyTarget {
  comment: PostComment;
  label: string;
}

/** コメントが描画されたら見える位置までスクロールする（scroll-margin で入力欄の裏に隠れない） */
function scrollToComment(commentId: string) {
  requestAnimationFrame(() => {
    document
      .getElementById(`comment-${commentId}`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

/** 返信先コメントが属するトップレベルのコメント ID */
function rootIdOf(comments: readonly PostComment[], comment: PostComment): string {
  const byId = new Map(comments.map((c) => [c.id, c]));
  let current = comment;
  const seen = new Set([current.id]);
  while (current.parent_comment_id) {
    const parent = byId.get(current.parent_comment_id);
    if (!parent || seen.has(parent.id)) break;
    seen.add(parent.id);
    current = parent;
  }
  return current.id;
}

/**
 * 投稿詳細のコメント一覧 + 固定フッターの入力欄。
 * - 一覧は時系列昇順。返信はトップレベルの下に 1 段インデント（Instagram 方式）
 * - Realtime で他ユーザー・キャラのコメントが即時に反映される
 * - 投稿後、キャラの自動返信が予約されたら「〇〇さんが返信を書いています…」を最大 30 秒表示
 */
export function PostComments({
  post,
  inputRef,
}: {
  post: Post;
  inputRef: RefObject<HTMLInputElement | null>;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: me } = useMyAccount();
  const { data: comments, isPending, isError, refetch, isRefetching } = useComments(post.id);
  const createComment = useCreateComment(post.id);
  const deleteComment = useDeleteComment(post.id);

  const [text, setText] = useState("");
  const [replyTo, setReplyTo] = useState<ReplyTarget | null>(null);
  const [pendingReply, setPendingReply] = useState<PendingReply | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [deleteTarget, setDeleteTarget] = useState<PostComment | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [footerHeight, setFooterHeight] = useState(0);

  const expandThread = useCallback((rootId: string) => {
    setCollapsed((prev) => {
      if (!prev.has(rootId)) return prev;
      const next = new Set(prev);
      next.delete(rootId);
      return next;
    });
  }, []);

  // キャラの返信が届いたら「返信を書いています…」を消す
  const pendingReplyRef = useRef<PendingReply | null>(null);
  useEffect(() => {
    pendingReplyRef.current = pendingReply;
  }, [pendingReply]);
  const onRealtimeInsert = useCallback(
    (comment: PostComment) => {
      const pending = pendingReplyRef.current;
      if (
        pending &&
        comment.author_type === "character" &&
        comment.parent_comment_id === pending.commentId
      ) {
        pendingReplyRef.current = null;
        setPendingReply(null);
        expandThread(pending.rootId);
        scrollToComment(comment.id);
      }
    },
    [expandThread],
  );
  useCommentsRealtime(post.id, { onInsert: onRealtimeInsert });

  // 返信が来ないまま 30 秒経ったら表示をやめる
  useEffect(() => {
    if (!pendingReply) return;
    const timer = setTimeout(() => setPendingReply(null), REPLY_WAIT_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [pendingReply]);

  const threads = useMemo(() => buildCommentThreads(comments ?? []), [comments]);
  const labelOf = useCallback((comment: PostComment) => commentAuthorLabel(comment, me), [me]);

  const startReply = (comment: PostComment) => {
    const label = labelOf(comment);
    setReplyTo({ comment, label });
    const mention = `@${label} `;
    setText((current) => (current.startsWith(mention) ? current : `${mention}${current}`));
    inputRef.current?.focus();
  };

  const submit = async () => {
    const body = text.trim();
    if (!body || createComment.isPending) return;
    const parent = replyTo?.comment ?? null;
    try {
      const response = await createComment.mutateAsync({
        body,
        parentCommentId: parent?.id ?? null,
      });
      setText("");
      setReplyTo(null);
      const rootId = parent ? rootIdOf(comments ?? [], parent) : response.comment.id;
      expandThread(rootId);
      // 返信が予約された場合は「返信を書いています…」を出す（Realtime で既に届いていれば出さない）
      const alreadyReplied = queryClient
        .getQueryData<PostComment[]>(queryKeys.comments(post.id))
        ?.some((c) => c.author_type === "character" && c.parent_comment_id === response.comment.id);
      if (response.reply_scheduled && !alreadyReplied) {
        const pending = { commentId: response.comment.id, rootId };
        // Realtime の返信が次の描画より先に届いても取りこぼさないよう、ref も同期的に更新する
        pendingReplyRef.current = pending;
        setPendingReply(pending);
      }
      // 追加したコメントが見えるようにスクロール
      scrollToComment(response.comment.id);
    } catch (error) {
      // モデレーション（422）等の想定内のエラーは warn（開発時のエラーオーバーレイを出さない）
      if (isApiError(error) && error.isClientError) {
        console.warn("[comments] create rejected:", error.code, error.message);
      } else {
        console.error("[comments] create failed:", error);
      }
      toast.error(getErrorMessage(error, "コメントを投稿できませんでした"));
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteComment.mutateAsync(deleteTarget.id);
      setDeleteOpen(false);
      toast.show("コメントを削除しました");
    } catch (error) {
      console.error("[comments] delete failed:", error);
      setDeleteOpen(false);
      toast.error("コメントを削除できませんでした。時間をおいて再度お試しください");
    }
  };

  const renderComment = (comment: PostComment, isReply: boolean) => (
    <CommentItem
      key={comment.id}
      comment={comment}
      label={labelOf(comment)}
      isPostAuthor={
        comment.author_type === "character" && comment.author_character_id === post.character_id
      }
      isOwn={isOwnComment(comment, me?.userId)}
      isReply={isReply}
      onReply={() => startReply(comment)}
      onDelete={() => {
        setDeleteTarget(comment);
        setDeleteOpen(true);
      }}
    />
  );

  return (
    <section
      aria-label="コメント"
      className="border-t border-ig-separator pt-2"
      style={
        {
          paddingBottom: `calc(${footerHeight + 12}px + env(safe-area-inset-bottom))`,
          "--composer-h": `${footerHeight}px`,
        } as CSSProperties
      }
    >
      {isPending ? (
        <CommentsSkeleton />
      ) : isError ? (
        <ErrorState
          compact
          message="コメントを読み込めませんでした"
          onRetry={() => void refetch()}
          retrying={isRefetching}
        />
      ) : threads.length === 0 ? (
        <div className="flex flex-col items-center px-8 py-10 text-center">
          <p className="text-[20px] leading-6 font-bold">まだコメントはありません</p>
          <p className="mt-2 text-[14px] text-ig-secondary">会話を始めましょう。</p>
        </div>
      ) : (
        <ul data-testid="comment-list">
          {threads.map((thread) => {
            const isCollapsed = collapsed.has(thread.root.id);
            const count = thread.replies.length;
            return (
              <li key={thread.root.id}>
                {renderComment(thread.root, false)}
                {count > 0 ? (
                  <button
                    type="button"
                    onClick={() =>
                      setCollapsed((prev) => {
                        const next = new Set(prev);
                        if (next.has(thread.root.id)) next.delete(thread.root.id);
                        else next.add(thread.root.id);
                        return next;
                      })
                    }
                    aria-expanded={!isCollapsed}
                    className="flex items-center gap-3 py-1.5 pl-[60px] text-[12px] leading-4 font-semibold text-ig-secondary pressable"
                  >
                    <span aria-hidden="true" className="h-px w-6 bg-ig-secondary" />
                    {isCollapsed ? `返信${count}件を表示` : "返信を非表示"}
                  </button>
                ) : null}
                {!isCollapsed ? thread.replies.map((reply) => renderComment(reply, true)) : null}
                {pendingReply?.rootId === thread.root.id ? (
                  <CommentTypingRow
                    name={post.character.name}
                    avatarUrl={post.character.avatar_url}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <CommentComposer
        inputRef={inputRef}
        value={text}
        onChange={(value) => {
          setText(value);
          // メンションを消したら返信モードも解除する
          if (replyTo && !value.startsWith(`@${replyTo.label}`)) setReplyTo(null);
        }}
        onSubmit={() => void submit()}
        submitting={createComment.isPending}
        myName={accountDisplayName(me)}
        replyingTo={replyTo?.label ?? null}
        onCancelReply={() => {
          if (replyTo) {
            const mention = `@${replyTo.label} `;
            setText((current) =>
              current.startsWith(mention) ? current.slice(mention.length) : current,
            );
          }
          setReplyTo(null);
        }}
        onHeightChange={setFooterHeight}
      />

      <Modal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="コメントを削除しますか？"
        description="削除したコメントは元に戻せません。"
        actions={[
          {
            label: "削除",
            variant: "destructive",
            onClick: () => void confirmDelete(),
            loading: deleteComment.isPending,
          },
          { label: "キャンセル", onClick: () => setDeleteOpen(false) },
        ]}
      />
    </section>
  );
}

function CommentsSkeleton() {
  return (
    <div aria-hidden="true" className="space-y-4 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex gap-3">
          <Skeleton shape="circle" className="size-8 shrink-0" />
          <div className="flex-1 space-y-2 pt-1">
            <Skeleton shape="text" className="w-24" />
            <Skeleton shape="text" className={i === 1 ? "w-3/5" : "w-4/5"} />
          </div>
        </div>
      ))}
    </div>
  );
}
