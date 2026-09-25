"use client";

import { useId, type KeyboardEvent } from "react";
import { cn } from "@/lib/cn";
import { MEMORY_LEVELS, type MemoryLevel } from "@/lib/queries/memories";

export interface PriorityControlProps {
  value: MemoryLevel;
  onChange: (level: MemoryLevel) => void;
  disabled?: boolean;
  /** アクセシブルな名前（既定「優先度」） */
  label?: string;
}

/** 優先度「低 / 中 / 高」のセグメントコントロール（radiogroup。←→ キーでも切り替え） */
export function PriorityControl({
  value,
  onChange,
  disabled = false,
  label = "優先度",
}: PriorityControlProps) {
  const labelId = useId();

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const index = MEMORY_LEVELS.findIndex((option) => option.level === value);
    const delta =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (delta === 0) return;
    event.preventDefault();
    const next = MEMORY_LEVELS[(index + delta + MEMORY_LEVELS.length) % MEMORY_LEVELS.length];
    if (next) {
      onChange(next.level);
      const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]");
      buttons[MEMORY_LEVELS.indexOf(next)]?.focus();
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      <span id={labelId} className="text-[12px] leading-4 text-ig-secondary">
        {label}
      </span>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        onKeyDown={onKeyDown}
        className={cn(
          "inline-flex rounded-lg border border-ig-sheet-separator p-0.5",
          disabled && "opacity-50",
        )}
      >
        {MEMORY_LEVELS.map((option) => {
          const selected = option.level === value;
          return (
            <button
              key={option.level}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={`優先度: ${option.label}`}
              tabIndex={selected ? 0 : -1}
              disabled={disabled}
              onClick={() => {
                if (!selected) onChange(option.level);
              }}
              className={cn(
                "h-6 min-w-8 rounded-md px-2 text-[13px] leading-none transition-colors",
                selected ? "bg-ig-text font-semibold text-ig-bg" : "text-ig-secondary",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
