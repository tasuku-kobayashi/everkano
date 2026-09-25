"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { HeartIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

/** API の上限（ChatRequest.message: 1〜2000 文字） */
export const MESSAGE_MAX_LENGTH = 2000;
/** 入力欄が自動で伸びる最大行数 */
const MAX_ROWS = 5;
const LINE_HEIGHT = 22;
const PADDING_Y = 9;

export interface MessageComposerProps {
  /** 送信（本文は trim 済み・空でない） */
  onSend: (body: string) => void;
  /** 返答待ちの間は送信できない（入力はできる） */
  sendDisabled: boolean;
  /** 高さが変わったとき（本文の下余白・トーストの位置調整用。safe-area を除く px） */
  onHeightChange?: (px: number) => void;
}

export interface MessageComposerHandle {
  focus: () => void;
}

/**
 * DM の入力欄（画面下に固定）。
 * - 角丸のグレーの入力欄・16px（iOS の自動ズーム防止）・「メッセージ…」
 * - 入力があるときだけ「送信」。空のときはハートを送れる（Instagram と同じ）
 * - 5 行まで自動で伸びる。PC（マウス操作）のみ Enter で送信、Shift+Enter で改行。IME 変換中は送信しない
 */
export const MessageComposer = forwardRef<MessageComposerHandle, MessageComposerProps>(
  function MessageComposer({ onSend, sendDisabled, onHeightChange }, ref) {
    const [text, setText] = useState("");
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const innerRef = useRef<HTMLDivElement>(null);

    useImperativeHandle(ref, () => ({ focus: () => textareaRef.current?.focus() }), []);

    const resize = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = "auto";
      const max = LINE_HEIGHT * MAX_ROWS + PADDING_Y * 2;
      const next = Math.min(el.scrollHeight, max);
      el.style.height = `${next}px`;
      el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
    }, []);

    useEffect(() => {
      resize();
    }, [text, resize]);

    // 入力欄の高さを親へ通知（本文の下余白・トーストの位置）
    useEffect(() => {
      const el = innerRef.current;
      if (!el || !onHeightChange) return;
      const report = () => onHeightChange(Math.round(el.getBoundingClientRect().height));
      report();
      if (typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(report);
      observer.observe(el);
      return () => observer.disconnect();
    }, [onHeightChange]);

    const trimmed = text.trim();
    const canSend = trimmed.length > 0 && !sendDisabled;

    const submit = () => {
      if (!canSend) return;
      onSend(trimmed);
      setText("");
    };

    const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
      // keyCode 229 = IME 処理中（Safari は isComposing が false のことがある）
      if (event.keyCode === 229) return;
      const finePointer =
        typeof window !== "undefined" && window.matchMedia?.("(pointer: fine)").matches;
      if (!finePointer) return; // スマホは Enter = 改行
      event.preventDefault();
      submit();
    };

    return (
      <div className="fixed inset-x-0 bottom-0 z-30 mx-auto max-w-[480px] bg-ig-bg pb-safe">
        <div ref={innerRef} className="px-3 pt-1.5 pb-2">
          <form
            className="flex min-h-11 items-end rounded-[22px] bg-ig-elevated pr-1 pl-4"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <label htmlFor="dm-composer" className="sr-only">
              メッセージ
            </label>
            <textarea
              id="dm-composer"
              ref={textareaRef}
              rows={1}
              value={text}
              maxLength={MESSAGE_MAX_LENGTH}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder="メッセージ…"
              enterKeyHint="enter"
              autoComplete="off"
              className="min-w-0 flex-1 resize-none bg-transparent text-[16px] leading-[22px] text-ig-text outline-none placeholder:text-ig-secondary focus-visible:outline-none"
              style={{ paddingTop: PADDING_Y, paddingBottom: PADDING_Y }}
            />
            {trimmed ? (
              <button
                type="submit"
                disabled={!canSend}
                // 送信ボタンを押してもキーボードを閉じない
                onPointerDown={(event) => event.preventDefault()}
                className={cn(
                  "mb-[5px] h-[34px] shrink-0 px-3 text-[15px] font-semibold text-ig-blue-text transition-opacity",
                  !canSend && "opacity-40",
                )}
              >
                送信
              </button>
            ) : (
              <button
                type="button"
                aria-label="ハートを送信"
                disabled={sendDisabled}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => {
                  if (!sendDisabled) onSend("❤️");
                }}
                className="mb-[5px] flex size-[34px] shrink-0 items-center justify-center text-ig-text pressable disabled:opacity-40"
              >
                <HeartIcon size={24} />
              </button>
            )}
          </form>
        </div>
      </div>
    );
  },
);
