"use client";

import { useId, useRef, useState, type ReactNode, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { CloseIcon } from "./icons";
import {
  FOCUSABLE_SELECTOR,
  useFocusTrap,
  useHistoryDismiss,
  useIsClient,
  usePresence,
  useScrollLock,
} from "./overlay";

export interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  /** シート上部の見出し（中央・太字）。省略時は aria-label を指定すること */
  title?: ReactNode;
  /** title が無い場合のアクセシブルな名前 */
  ariaLabel?: string;
  children: ReactNode;
  /** シート本体に追加するクラス（高さ等） */
  className?: string;
  /** 本文（スクロール領域）に追加するクラス */
  bodyClassName?: string;
  /** 下端に固定するフッター（保存ボタンなど） */
  footer?: ReactNode;
  /**
   * 右上に「閉じる」（×）ボタンを出す。既定: title があるとき true。
   * VoiceOver / TalkBack の利用者には Esc も背景タップ（aria-hidden）もドラッグも無く、フォーカストラップで
   * シートの外にも出られないため、閉じる操作がシート内に無いと閉じられなくなる。
   * 最下段に「キャンセル」行を持つシート（ActionSheet）だけ false にしてよい。
   */
  showClose?: boolean;
}

/** これ以上下にドラッグしたら閉じる（px） */
const DISMISS_THRESHOLD = 90;

/**
 * 開いたときの初期フォーカス: 「閉じる」ボタンを除いた最初のフォーカス可能要素（入力欄・主な操作）。
 * 無ければ null（= シート自体にフォーカスし、スクリーンリーダーは見出しを読み上げる）。
 * 「閉じる」は DOM 上は見出しの直後（読み上げ順が自然）だが、開いた瞬間にそこへフォーカスすると
 * 表示名の入力欄などにすぐ入力できなくなるため。
 */
function initialSheetFocus(container: HTMLElement): HTMLElement | null {
  const candidates = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
  return (
    candidates.find((el) => !el.hasAttribute("data-sheet-close") && el.offsetParent !== null) ??
    null
  );
}

/**
 * Instagram 風ボトムシート。
 * - ドラッグハンドル（下にスワイプで閉じる）、背景タップ・Esc・端末の「戻る」・右上の「閉じる」で閉じる
 * - 簡易フォーカストラップ、body スクロールロック、safe-area 対応
 */
export function BottomSheet({
  open,
  onClose,
  title,
  ariaLabel,
  children,
  className,
  bodyClassName,
  footer,
  showClose = Boolean(title),
}: BottomSheetProps) {
  const isClient = useIsClient();
  const { mounted, visible } = usePresence(open);
  const sheetRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<number | null>(null);

  useScrollLock(mounted);
  useFocusTrap(sheetRef, open && mounted, onClose, { initialFocus: initialSheetFocus });
  useHistoryDismiss(open, onClose);

  if (!isClient || !mounted) return null;

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    dragStart.current = event.clientY;
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (dragStart.current === null) return;
    setDragY(Math.max(0, event.clientY - dragStart.current));
  };
  const onPointerEnd = () => {
    if (dragStart.current === null) return;
    dragStart.current = null;
    setDragging(false);
    if (dragY > DISMISS_THRESHOLD) onClose();
    setDragY(0);
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-center" role="presentation">
      <div
        aria-hidden="true"
        onClick={onClose}
        className={cn(
          "absolute inset-0 bg-ig-overlay transition-opacity duration-200",
          visible ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : ariaLabel}
        tabIndex={-1}
        style={{
          transform: visible ? `translateY(${dragY}px)` : "translateY(100%)",
          transition: dragging ? "none" : undefined,
        }}
        className={cn(
          "absolute bottom-0 flex max-h-[85dvh] w-full max-w-[480px] flex-col rounded-t-2xl bg-ig-sheet text-ig-text shadow-[0_-4px_24px_rgb(0_0_0/0.12)] transition-transform duration-200 ease-out outline-none",
          className,
        )}
      >
        <div
          className="flex shrink-0 cursor-grab touch-none flex-col items-center pt-2 active:cursor-grabbing"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
        >
          <span aria-hidden="true" className="h-1 w-10 rounded-full bg-ig-secondary/40" />
          {title ? (
            <h2
              id={titleId}
              className={cn(
                "w-full border-b border-ig-sheet-separator pt-3 pb-3 text-center text-[16px] font-bold",
                // 右上の「閉じる」と重ならないよう、左右を同じだけ空けて中央揃えを保つ
                showClose ? "px-12" : "px-4",
              )}
            >
              {title}
            </h2>
          ) : (
            <span className={showClose ? "h-9" : "h-2"} />
          )}
        </div>
        {showClose ? (
          // ドラッグ領域（setPointerCapture する要素）の外に置く。中に置くとタップがドラッグに奪われる
          <button
            type="button"
            data-sheet-close=""
            aria-label="閉じる"
            onClick={onClose}
            className="absolute top-3 right-2 flex size-10 items-center justify-center rounded-full text-ig-text pressable"
          >
            <CloseIcon size={22} strokeWidth={2} />
          </button>
        ) : null}
        <div className={cn("min-h-0 flex-1 overflow-y-auto overscroll-contain", bodyClassName)}>
          {children}
        </div>
        {footer ? <div className="shrink-0 px-4 pt-2">{footer}</div> : null}
        <div className="shrink-0 pb-safe">
          <div className="h-3" />
        </div>
      </div>
    </div>,
    document.body,
  );
}
