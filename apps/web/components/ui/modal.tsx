"use client";

import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { Spinner } from "./spinner";
import { useFocusTrap, useIsClient, usePresence, useScrollLock } from "./overlay";

export interface ModalAction {
  label: string;
  onClick: () => void;
  /** destructive: 赤太字 / primary: 青太字 / default: 通常 */
  variant?: "default" | "primary" | "destructive";
  disabled?: boolean;
  loading?: boolean;
}

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  /** 見出しの上に表示するアイコン等 */
  icon?: ReactNode;
  /** 本文（任意のコンテンツ） */
  children?: ReactNode;
  /** 下部に縦に並ぶボタン（Instagram の確認ダイアログ形式）。区切り線付き */
  actions?: ModalAction[];
  /** 背景タップで閉じない（処理中など） */
  dismissible?: boolean;
  className?: string;
}

const ACTION_CLASSES: Record<NonNullable<ModalAction["variant"]>, string> = {
  default: "font-normal text-ig-text",
  primary: "font-bold text-ig-blue",
  destructive: "font-bold text-ig-red",
};

/**
 * Instagram 風の中央ダイアログ（「退会しますか？」「有料コンテンツです」等）。
 * 背景タップ・Esc で閉じる（dismissible=false で無効化）。
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  icon,
  children,
  actions,
  dismissible = true,
  className,
}: ModalProps) {
  const isClient = useIsClient();
  const { mounted, visible } = usePresence(open, 150);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const close = () => {
    if (dismissible) onClose();
  };

  useScrollLock(mounted);
  useFocusTrap(dialogRef, open && mounted, close);

  if (!isClient || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-8" role="presentation">
      <div
        aria-hidden="true"
        onClick={close}
        className={cn(
          "absolute inset-0 bg-ig-overlay transition-opacity duration-150",
          visible ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative w-full max-w-[400px] overflow-hidden rounded-xl bg-ig-sheet text-ig-text transition-[opacity,transform] duration-150 ease-out outline-none",
          visible ? "scale-100 opacity-100" : "scale-110 opacity-0",
          className,
        )}
      >
        {icon || title || description || children ? (
          <div className="flex flex-col items-center px-6 pt-7 pb-6 text-center">
            {icon ? <div className="mb-4">{icon}</div> : null}
            {title ? (
              <h2 id={titleId} className="text-[18px] leading-6 font-semibold">
                {title}
              </h2>
            ) : null}
            {description ? (
              <p
                id={descriptionId}
                className="mt-2 text-[14px] leading-[18px] text-balance text-ig-secondary"
              >
                {description}
              </p>
            ) : null}
            {children ? <div className="mt-4 w-full">{children}</div> : null}
          </div>
        ) : null}
        {actions?.length ? (
          <div className="flex flex-col">
            {actions.map((action) => (
              <button
                key={action.label}
                type="button"
                onClick={action.onClick}
                disabled={action.disabled || action.loading}
                className={cn(
                  "flex min-h-12 items-center justify-center border-t border-ig-sheet-separator px-4 py-3 text-[14px] enabled:active:bg-ig-elevated disabled:opacity-50",
                  ACTION_CLASSES[action.variant ?? "default"],
                )}
              >
                {action.loading ? <Spinner size={18} label="処理中" /> : action.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
