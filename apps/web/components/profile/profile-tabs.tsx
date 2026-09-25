"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { GridIcon, LockIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import type { CharacterPostsTab } from "@/lib/queries/keys";

const TABS: { value: CharacterPostsTab; label: string; icon: ReactNode }[] = [
  { value: "free", label: "無料", icon: <GridIcon size={22} strokeWidth={1.8} /> },
  { value: "paid", label: "有料", icon: <LockIcon size={22} strokeWidth={1.8} /> },
];

/** プロフィールの「無料」/「有料」タブ（Instagram のグリッドタブ: アイコン + 選択中は下線） */
export function ProfileTabs({
  value,
  onChange,
}: {
  value: CharacterPostsTab;
  onChange: (value: CharacterPostsTab) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  // ←→ キーでタブ移動（WAI-ARIA Tabs パターン）
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const index = TABS.findIndex((tab) => tab.value === value);
    const nextIndex = (index + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    const next = TABS[nextIndex];
    if (!next) return;
    onChange(next.value);
    refs.current[nextIndex]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="投稿の種類"
      onKeyDown={onKeyDown}
      className="bg-ig-bg top-header border-ig-separator sticky z-20 mt-4 flex border-b"
    >
      {TABS.map((tab, index) => {
        const active = tab.value === value;
        return (
          <button
            key={tab.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`profile-tab-${tab.value}`}
            aria-selected={active}
            aria-controls={`profile-panel-${tab.value}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(tab.value)}
            className={cn(
              "relative flex h-11 flex-1 items-center justify-center gap-1.5 text-[13px] font-semibold transition-colors",
              active ? "text-ig-text" : "text-ig-secondary",
            )}
            data-testid={`profile-tab-${tab.value}`}
          >
            {tab.icon}
            <span>{tab.label}</span>
            {active ? (
              <span aria-hidden="true" className="bg-ig-text absolute inset-x-0 -bottom-px h-px" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
