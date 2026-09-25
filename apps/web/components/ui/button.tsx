import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonStyleOptions {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  // 青ボタン（ログイン・フォロー相当・DMする等）
  primary: "bg-ig-blue text-white enabled:active:bg-ig-blue-hover disabled:opacity-40",
  // グレーボタン（プロフィールを編集・メッセージ等）
  secondary: "bg-ig-elevated text-ig-text enabled:active:bg-ig-elevated-hover disabled:opacity-50",
  // 背景なしの青テキスト（「再送信」「すべて見る」等）
  ghost: "bg-transparent text-ig-blue enabled:active:opacity-50 disabled:opacity-40",
  // 背景なしの赤テキスト（退会・削除などの破壊的操作）
  danger: "bg-transparent text-ig-red enabled:active:opacity-50 disabled:opacity-40",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[14px] rounded-lg",
  md: "h-9 px-4 text-[14px] rounded-lg",
  lg: "h-11 px-4 text-[15px] rounded-xl",
};

/** <Link> などをボタン風にする場合に使うクラス */
export function buttonClassName({
  variant = "primary",
  size = "md",
  fullWidth = false,
}: ButtonStyleOptions = {}): string {
  return cn(
    "relative inline-flex select-none items-center justify-center gap-1.5 font-semibold leading-none transition-[opacity,background-color] duration-100",
    VARIANT_CLASSES[variant],
    SIZE_CLASSES[size],
    fullWidth && "w-full",
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, ButtonStyleOptions {
  /** true の間はスピナーを表示し、クリックできない */
  loading?: boolean;
}

/** Instagram 風ボタン（primary: 青 / secondary: グレー / ghost: 青テキスト / danger: 赤テキスト） */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, fullWidth, loading = false, disabled, className, children, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(buttonClassName({ variant, size, fullWidth }), className)}
      {...rest}
    >
      <span className={cn("inline-flex items-center gap-1.5", loading && "invisible")}>
        {children}
      </span>
      {loading ? (
        <span className="absolute inset-0 flex items-center justify-center">
          <Spinner size={18} label="処理中" />
        </span>
      ) : null}
    </button>
  );
});
