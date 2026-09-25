"use client";

import { useId, useRef, useState, type ReactNode, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { useFocusTrap, useIsClient, usePresence, useScrollLock } from "./overlay";

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
}

/** これ以上下にドラッグしたら閉じる（px） */
const DISMISS_THRESHOLD = 90;

/**
 * Instagram 風ボトムシート。
 * - ドラッグハンドル（下にスワイプで閉じる）、背景タップ・Esc で閉じる
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
}: BottomSheetProps) {
  const isClient = useIsClient();
  const { mounted, visible } = usePresence(open);
  const sheetRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<number | null>(null);

  useScrollLock(mounted);
  useFocusTrap(sheetRef, open && mounted, onClose);

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
              className="w-full border-b border-ig-sheet-separator px-4 pt-3 pb-3 text-center text-[16px] font-bold"
            >
              {title}
            </h2>
          ) : (
            <span className="h-2" />
          )}
        </div>
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
