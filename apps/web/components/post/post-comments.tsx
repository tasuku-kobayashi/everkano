"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  memo,
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
  COMMENTS_LIMIT,
  isOwnComment,
  replyMentionFor,
  threadRootId,
  useComments,
  useCommentsRealtime,
  useCreateComment,
  useDeleteComment,
  type CommentThread,
  type PostComment,
} from "@/lib/queries/comments";
import { queryKeys } from "@/lib/queries/keys";
import type { Post, PostAuthor } from "@/lib/queries/posts";
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
  /** 「〇〇さんに返信中」に出す名前（本人の画面にだけ出る。自分のコメントなら自分の表示名） */
  label: string;
  /**
   * 本文の先頭に入れる @メンション（本文として保存され全ユーザーに見えるため、公開名 = handle /
   * user_xxxxxx のみ。自分のコメントへの返信では入れない = null）
   */
  mention: string | null;
}

const mentionPrefix = (mention: string) => `@${mention} `;

/**
 * コメントが描画されたら見える位置までスクロールする（scroll-margin で入力欄の裏に隠れない）。
 * キャッシュの更新が画面に反映されるのは次のタスク以降なので、要素が現れるまで数フレーム待つ。
 */
function scrollToComment(commentId: string, attempts = 10) {
  requestAnimationFrame(() => {
    const element = document.getElementById(`comment-${commentId}`);
    if (element) element.scrollIntoView({ block: "nearest", behavior: "smooth" });
    else if (attempts > 1) scrollToComment(commentId, attempts - 1);
  });
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

  // 返信先は安定したコールバック（startReply 等）から読むため ref にも同期的に持つ
  const replyToRef = useRef<ReplyTarget | null>(null);
  const changeReplyTo = useCallback((next: ReplyTarget | null) => {
    replyToRef.current = next;
    setReplyTo(next);
  }, []);

  const expandThread = useCallback((rootId: string) => {
    setCollapsed((prev) => {
      if (!prev.has(rootId)) return prev;
      const next = new Set(prev);
      next.delete(rootId);
      return next;
    });
  }, []);

  const toggleThread = useCallback((rootId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId);
      else next.add(rootId);
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
  const myUserId = me?.userId;
  const labelOf = useCallback((comment: PostComment) => commentAuthorLabel(comment, me), [me]);

  const startReply = useCallback(
    (comment: PostComment) => {
      const previous = replyToRef.current?.mention ?? null;
      const mention = replyMentionFor(comment, myUserId);
      changeReplyTo({ comment, label: labelOf(comment), mention });
      setText((current) => {
        // 別のコメントへの返信に切り替えたら、前の返信先のメンションは外す
        let body = current;
        if (previous && body.startsWith(mentionPrefix(previous))) {
          body = body.slice(mentionPrefix(previous).length);
        }
        if (!mention) return body;
        const prefix = mentionPrefix(mention);
        return body.startsWith(prefix) ? body : `${prefix}${body}`;
      });
      inputRef.current?.focus();
    },
    [changeReplyTo, labelOf, myUserId, inputRef],
  );

  const requestDelete = useCallback((comment: PostComment) => {
    setDeleteTarget(comment);
    setDeleteOpen(true);
  }, []);

  const changeText = (value: string) => {
    setText(value);
    // メンションを消したら返信モードも解除する
    const target = replyToRef.current;
    if (target?.mention && !value.startsWith(`@${target.mention}`)) changeReplyTo(null);
  };

  const cancelReply = () => {
    const mention = replyToRef.current?.mention;
    if (mention) {
      const prefix = mentionPrefix(mention);
      setText((current) => (current.startsWith(prefix) ? current.slice(prefix.length) : current));
    }
    changeReplyTo(null);
  };

  const submit = async () => {
    const submitted = text;
    const body = submitted.trim();
    if (!body || createComment.isPending) return;
    const target = replyToRef.current;
    const parent = target?.comment ?? null;
    try {
      const response = await createComment.mutateAsync({
        body,
        parentCommentId: parent?.id ?? null,
      });
      // 送信中に書き足した・書き換えた内容は消さない（送った内容のままのときだけ空にする）
      setText((current) => (current === submitted ? "" : current));
      if (replyToRef.current === target) changeReplyTo(null);
      const rootId = parent ? threadRootId(comments ?? [], parent) : response.comment.id;
      expandThread(rootId);
      // 返信が予約された場合は「返信を書いています…」を出す（Realtime で既に届いていれば出さない）
      const reply = queryClient
        .getQueryData<PostComment[]>(queryKeys.comments(post.id))
        ?.find((c) => c.author_type === "character" && c.parent_comment_id === response.comment.id);
      if (response.reply_scheduled && !reply) {
        const pending = { commentId: response.comment.id, rootId };
        // Realtime の返信が次の描画より先に届いても取りこぼさないよう、ref も同期的に更新する
        pendingReplyRef.current = pending;
        setPendingReply(pending);
        // スクロールは「返信を書いています…」の行が表示時に自分で行う（投稿したコメントのすぐ下の行。
        // ここで投稿したコメントへスクロールすると、その行が入力欄の裏に隠れたままになる）
      } else {
        // 追加したコメント（返信が既に届いていれば、そのすぐ下の返信）が見えるようにスクロール
        scrollToComment(reply?.id ?? response.comment.id);
      }
    } catch (error) {
      // モデレーション（422）等の想定内のエラーは warn（開発時のエラーオーバーレイを出さない）
      if (isApiError(error) && error.isClientError) {
        console.warn("[comments] create rejected:", error.code, error.message);
      } else {
        console.error("[comments] create failed:", error);
      }
      toast.error(getErrorMessage(error, "コメントを投稿できませんでした。"));
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
      toast.error("コメントを削除できませんでした。しばらくしてから再度お試しください。");
    }
  };

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
      ) : isError && !comments ? (
        // 再取得の失敗（data は残る）では読み込み済みのコメントを表示し続ける
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
        <>
          {(comments?.length ?? 0) >= COMMENTS_LIMIT ? (
            <p
              className="px-4 py-2 text-center text-[12px] leading-4 text-ig-secondary"
              data-testid="comments-limited"
            >
              最新の{COMMENTS_LIMIT.toLocaleString("ja-JP")}件より前のコメントは表示されません
            </p>
          ) : null}
          <CommentThreads
            threads={threads}
            collapsed={collapsed}
            onToggleThread={toggleThread}
            pendingReply={pendingReply}
            labelOf={labelOf}
            myUserId={myUserId}
            postCharacter={post.character}
            onReply={startReply}
            onDelete={requestDelete}
          />
        </>
      )}

      <CommentComposer
        inputRef={inputRef}
        value={text}
        onChange={changeText}
        onSubmit={() => void submit()}
        submitting={createComment.isPending}
        myName={accountDisplayName(me)}
        replyingTo={replyTo?.label ?? null}
        onCancelReply={cancelReply}
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

interface CommentThreadsProps {
  threads: readonly CommentThread[];
  collapsed: ReadonlySet<string>;
  onToggleThread: (rootId: string) => void;
  pendingReply: PendingReply | null;
  labelOf: (comment: PostComment) => string;
  myUserId: string | undefined;
  /** 投稿者のキャラ（「作成者」表示・「返信を書いています…」） */
  postCharacter: PostAuthor;
  onReply: (comment: PostComment) => void;
  onDelete: (comment: PostComment) => void;
}

/**
 * コメント一覧（スレッド）。memo 化して、入力欄への入力（1 文字ごとの state 更新）では再描画しない。
 * props はすべて安定した参照（useMemo / useCallback / state）で渡すこと。
 */
const CommentThreads = memo(function CommentThreads({
  threads,
  collapsed,
  onToggleThread,
  pendingReply,
  labelOf,
  myUserId,
  postCharacter,
  onReply,
  onDelete,
}: CommentThreadsProps) {
  const renderComment = (comment: PostComment, isReply: boolean) => (
    <CommentItem
      key={comment.id}
      comment={comment}
      label={labelOf(comment)}
      isPostAuthor={
        comment.author_type === "character" && comment.author_character_id === postCharacter.id
      }
      isOwn={isOwnComment(comment, myUserId)}
      isReply={isReply}
      onReply={onReply}
      onDelete={onDelete}
    />
  );

  return (
    <ul data-testid="comment-list">
      {threads.map((thread) => {
        const isCollapsed = collapsed.has(thread.root.id);
        const count = thread.replies.length;
        // 「返信を書いています…」は投稿したコメントが一覧に現れてから出す（その下に並べ、
        // 表示時のスクロールで両方が見えるようにする）
        const showTyping =
          pendingReply?.rootId === thread.root.id &&
          (thread.root.id === pendingReply.commentId ||
            thread.replies.some((reply) => reply.id === pendingReply.commentId));
        return (
          <li key={thread.root.id}>
            {renderComment(thread.root, false)}
            {count > 0 ? (
              <button
                type="button"
                onClick={() => onToggleThread(thread.root.id)}
                aria-expanded={!isCollapsed}
                className="flex items-center gap-3 py-1.5 pl-[60px] text-[12px] leading-4 font-semibold text-ig-secondary pressable"
              >
                <span aria-hidden="true" className="h-px w-6 bg-ig-secondary" />
                {isCollapsed ? `返信${count}件を表示` : "返信を非表示"}
              </button>
            ) : null}
            {!isCollapsed ? thread.replies.map((reply) => renderComment(reply, true)) : null}
            {showTyping ? (
              <CommentTypingRow
                // 同じスレッドで続けて投稿したときも表示し直してスクロールさせる
                key={pendingReply.commentId}
                name={postCharacter.name}
                avatarUrl={postCharacter.avatar_url}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
});

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
