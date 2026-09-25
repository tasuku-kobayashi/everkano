"use client";

import { useLayoutEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { Avatar } from "@/components/ui/avatar";
import { CloseIcon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { useBottomBarHeight } from "@/components/ui/toast";

/** コメント本文の最大文字数（API: 1〜500 文字） */
export const COMMENT_MAX_LENGTH = 500;

/** Instagram のコメント入力欄の上に並ぶクイック絵文字 */
const QUICK_EMOJIS = ["❤️", "🙌", "🔥", "👏", "😢", "😍", "😮", "😂"] as const;

export interface CommentComposerProps {
  inputRef: RefObject<HTMLInputElement | null>;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  /** 自分の表示名（アバターの頭文字） */
  myName: string;
  /** 返信先（「〇〇さんに返信中」バー） */
  replyingTo: string | null;
  onCancelReply: () => void;
  /** フッターの高さ（safe-area を除く）が変わったとき。本文の下余白に使う */
  onHeightChange: (height: number) => void;
}

/**
 * 投稿詳細の固定フッター（コメント入力欄）。タブバーは非表示の画面なので画面最下部に固定する。
 * safe-area 対応・入力欄は 16px（iOS のズーム防止）。空のときは「投稿する」を押せない。
 */
export function CommentComposer({
  inputRef,
  value,
  onChange,
  onSubmit,
  submitting,
  myName,
  replyingTo,
  onCancelReply,
  onHeightChange,
}: CommentComposerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);
  const canSubmit = value.trim().length > 0 && !submitting;

  // トーストを入力欄の上に出す
  useBottomBarHeight(height);

  useLayoutEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const measure = () => {
      // pb-safe 分を除いた高さ（safe-area は呼び出し側で env() として加算する）
      const paddingBottom = Number.parseFloat(getComputedStyle(node).paddingBottom) || 0;
      const next = Math.round(node.getBoundingClientRect().height - paddingBottom);
      setHeight(next);
      onHeightChange(next);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [onHeightChange]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (canSubmit) onSubmit();
  };

  const insertEmoji = (emoji: string) => {
    const next = `${value}${emoji}`;
    if (next.length > COMMENT_MAX_LENGTH) return;
    onChange(next);
    inputRef.current?.focus();
  };

  return (
    <div
      ref={containerRef}
      className="pb-safe bg-ig-bg border-ig-separator fixed inset-x-0 bottom-0 z-30 mx-auto max-w-[480px] border-t"
    >
      {replyingTo ? (
        <div className="bg-ig-elevated text-ig-secondary flex h-10 items-center justify-between pr-2 pl-4 text-[13px]">
          <span className="truncate">{replyingTo}さんに返信中</span>
          <button
            type="button"
            onClick={onCancelReply}
            aria-label="返信をキャンセル"
            className="pressable flex size-8 items-center justify-center"
          >
            <CloseIcon size={16} />
          </button>
        </div>
      ) : null}
      <div className="flex items-center justify-between px-4 pt-2">
        {QUICK_EMOJIS.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={() => insertEmoji(emoji)}
            aria-label={`${emoji}を入力`}
            className="pressable flex size-9 items-center justify-center text-[24px] leading-none"
          >
            {emoji}
          </button>
        ))}
      </div>
      <form onSubmit={handleSubmit} className="flex items-center gap-3 px-4 py-2">
        <Avatar src={null} alt={myName || "自分"} size="sm" />
        <div className="border-ig-input-border flex h-11 min-w-0 flex-1 items-center rounded-full border pr-1 pl-4">
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="コメントを追加…"
            aria-label="コメントを追加"
            maxLength={COMMENT_MAX_LENGTH}
            enterKeyHint="send"
            autoComplete="off"
            className="placeholder:text-ig-secondary min-w-0 flex-1 bg-transparent text-[16px] outline-none"
            data-testid="comment-input"
          />
          <button
            type="submit"
            disabled={!canSubmit}
            className="text-ig-blue flex h-9 shrink-0 items-center px-3 text-[14px] font-semibold disabled:opacity-40"
            data-testid="comment-submit"
          >
            {submitting ? <Spinner size={18} label="投稿中" /> : "投稿する"}
          </button>
        </div>
      </form>
    </div>
  );
}
