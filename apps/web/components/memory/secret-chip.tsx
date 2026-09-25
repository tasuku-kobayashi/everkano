"use client";

import { LockFilledIcon, LockIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

export interface SecretChipProps {
  active: boolean;
  onToggle: (next: boolean) => void;
  disabled?: boolean;
}

/** 「二人だけの秘密」タグのトグル（aria-pressed） */
export function SecretChip({ active, onToggle, disabled = false }: SecretChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={() => onToggle(!active)}
      className={cn(
        "inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2.5 text-[12px] leading-none font-semibold transition-colors disabled:opacity-50",
        active ? "brand-gradient text-white" : "border border-ig-sheet-separator text-ig-secondary",
      )}
    >
      {active ? <LockFilledIcon size={13} /> : <LockIcon size={13} />}
      二人だけの秘密
    </button>
  );
}
