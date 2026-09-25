"use client";

import { cn } from "@/lib/cn";
import { Button } from "./button";
import { AlertIcon } from "./icons";

export interface ErrorStateProps {
  /** 既定「読み込めませんでした」 */
  title?: string;
  /** 画面に出すメッセージ（ApiError.message など）。既定は通信エラーの案内 */
  message?: string;
  /** 指定すると「再読み込み」ボタンを表示 */
  onRetry?: () => void;
  /** 再試行中（ボタンをローディング表示） */
  retrying?: boolean;
  className?: string;
  /** compact: リスト末尾など小さな領域用 */
  compact?: boolean;
}

/** 読み込み失敗の表示（リトライボタン付き） */
export function ErrorState({
  title = "読み込めませんでした",
  message = "通信状況を確認して、しばらくしてから再度お試しください。",
  onRetry,
  retrying = false,
  className,
  compact = false,
}: ErrorStateProps) {
  if (compact) {
    return (
      <div
        role="alert"
        className={cn("flex flex-col items-center gap-2 px-6 py-6 text-center", className)}
      >
        <p className="text-[14px] text-ig-secondary">{message}</p>
        {onRetry ? (
          <Button variant="ghost" size="sm" onClick={onRetry} loading={retrying}>
            再読み込み
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <div
      role="alert"
      className={cn("flex flex-col items-center px-8 py-14 text-center", className)}
    >
      <div className="mb-4 flex size-[62px] items-center justify-center rounded-full border-2 border-ig-text">
        <AlertIcon size={30} strokeWidth={1.6} />
      </div>
      <h2 className="text-[20px] leading-6 font-bold text-balance [word-break:auto-phrase]">
        {title}
      </h2>
      <p className="mt-2 max-w-[320px] text-[14px] leading-[18px] text-balance [word-break:auto-phrase] text-ig-secondary">
        {message}
      </p>
      {onRetry ? (
        <Button className="mt-5" variant="secondary" onClick={onRetry} loading={retrying}>
          再読み込み
        </Button>
      ) : null}
    </div>
  );
}
