"use client";

import type { MemoryKind } from "@everkano/shared";
import { useId } from "react";
import { HistoryIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  EDITABLE_MEMORY_KINDS,
  memoryKindLabel,
  type MemoryKindFilter,
  type MemoryKindOption,
} from "@/lib/queries/memories";

/** 記憶の種類のラベル（記憶 1 件の見出し行） */
export function MemoryKindChip({ kind }: { kind: MemoryKind }) {
  return (
    <span
      className="inline-flex h-5 shrink-0 items-center rounded border border-ig-sheet-separator px-1.5 text-[11px] leading-none font-semibold text-ig-secondary"
      data-testid="memory-kind"
    >
      {kind === "summary" ? "🗒 " : ""}
      {memoryKindLabel(kind)}
    </span>
  );
}

/** 記憶の種類の選択（追加・編集。会話の要約は選べない） */
export function MemoryKindSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: MemoryKind;
  onChange: (kind: MemoryKind) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor={id} className="text-[12px] leading-4 text-ig-secondary">
        種類
      </label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as MemoryKind)}
        // iOS の拡大を防ぐため 16px（見た目は小さめのピル）
        className="h-8 rounded-lg border border-ig-sheet-separator bg-ig-sheet px-2 text-[16px] leading-none text-ig-text outline-none focus:border-ig-secondary disabled:opacity-50"
      >
        {EDITABLE_MEMORY_KINDS.map((option) => (
          <option key={option.kind} value={option.kind}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

const CHIP =
  "inline-flex h-8 shrink-0 items-center gap-1 rounded-lg px-3 text-[13px] leading-none font-semibold transition-colors";

/**
 * 種類で絞り込むチップの行（横スクロール）+「以前の記憶」の表示切り替え。
 * 選択中のチップは塗り（aria-pressed）。記憶のある種類だけを出す。
 */
export function MemoryKindFilterBar({
  options,
  value,
  onChange,
  supersededCount,
  showSuperseded,
  onToggleSuperseded,
}: {
  options: readonly MemoryKindOption[];
  value: MemoryKindFilter;
  onChange: (value: MemoryKindFilter) => void;
  /** 以前の記憶（置き換えられた記憶）の件数。0 なら切り替えを出さない */
  supersededCount: number;
  showSuperseded: boolean;
  onToggleSuperseded: (next: boolean) => void;
}) {
  const chips: Array<{ value: MemoryKindFilter; label: string }> = [
    { value: "all", label: "すべて" },
    ...options.map((option) => ({ value: option.kind, label: option.label })),
  ];
  return (
    <div
      className="scrollbar-none flex gap-2 overflow-x-auto px-4 pt-1 pb-3"
      role="group"
      aria-label="記憶の種類で絞り込む"
    >
      {chips.map((chip) => {
        const selected = chip.value === value;
        return (
          <button
            key={chip.value}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(chip.value)}
            className={cn(
              CHIP,
              selected ? "bg-ig-text text-ig-bg" : "bg-ig-elevated text-ig-text pressable",
            )}
          >
            {chip.label}
          </button>
        );
      })}
      {supersededCount > 0 ? (
        <button
          type="button"
          aria-pressed={showSuperseded}
          onClick={() => onToggleSuperseded(!showSuperseded)}
          className={cn(
            CHIP,
            "border",
            showSuperseded
              ? "border-ig-text bg-ig-text text-ig-bg"
              : "border-ig-sheet-separator text-ig-text pressable",
          )}
          data-testid="superseded-toggle"
        >
          <HistoryIcon size={14} strokeWidth={2.2} />
          以前の記憶
          <span className="font-normal">{supersededCount}</span>
        </button>
      ) : null}
    </div>
  );
}
