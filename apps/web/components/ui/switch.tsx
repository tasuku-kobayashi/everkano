"use client";

import { cn } from "@/lib/cn";

export interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  /** 見えるラベルの要素の id（aria-labelledby）。無ければ label を指定する */
  labelledBy?: string;
  /** アクセシブルな名前（見えるラベルが無い場合） */
  label?: string;
  /** 補足説明の要素の id（aria-describedby） */
  describedBy?: string;
  className?: string;
  "data-testid"?: string;
}

/**
 * Instagram の設定画面と同じオン・オフのスイッチ（role="switch"）。
 * オンは塗り（--ig-text）、オフはグレー。つまみの位置でも状態が分かる（色だけに頼らない）。
 */
export function Switch({
  checked,
  onChange,
  disabled = false,
  labelledBy,
  label,
  describedBy,
  className,
  "data-testid": testId,
}: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-label={labelledBy ? undefined : label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      data-testid={testId}
      className={cn(
        // タップ領域は 44px 以上（見た目のトラックは 51×31。左右に少し余白）
        "relative inline-flex h-11 w-[60px] shrink-0 items-center justify-center disabled:opacity-40",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "relative h-[31px] w-[51px] rounded-full transition-colors duration-150",
          checked ? "bg-ig-text" : "bg-ig-elevated-hover",
        )}
      >
        <span
          className={cn(
            "absolute top-[2px] left-[2px] size-[27px] rounded-full bg-ig-bg shadow-[0_2px_4px_rgb(0_0_0/0.2)] transition-transform duration-150",
            checked && "translate-x-5",
          )}
        />
      </span>
    </button>
  );
}
