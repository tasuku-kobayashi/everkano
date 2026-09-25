"use client";

import { forwardRef, useState } from "react";
import { SearchIcon } from "@/components/ui/icons";

export interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  /** 「キャンセル」: 入力を消してフォーカスを外す */
  onCancel: () => void;
}

/**
 * Instagram の検索バー（角丸のグレーの入力欄 + 虫眼鏡。入力中は右にクリアボタンと「キャンセル」）。
 * 画面上部に sticky で固定する（safe-area 対応）。
 */
export const SearchBar = forwardRef<HTMLInputElement, SearchBarProps>(function SearchBar(
  { value, onChange, onCancel },
  ref,
) {
  const [focused, setFocused] = useState(false);
  const showCancel = focused || value.length > 0;

  return (
    <div className="sticky top-0 z-30 bg-ig-bg pt-safe">
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          // 送信（キーボードの「検索」）でキーボードを閉じる
          (document.activeElement as HTMLElement | null)?.blur();
        }}
        className="flex h-[52px] items-center gap-3 px-4"
      >
        <label className="relative flex h-9 min-w-0 flex-1 items-center rounded-[10px] bg-ig-elevated">
          <span className="pointer-events-none absolute left-3 flex text-ig-secondary">
            <SearchIcon size={16} strokeWidth={2.4} />
          </span>
          <input
            ref={ref}
            type="search"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder="検索"
            aria-label="キャラクターを検索"
            enterKeyHint="search"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={100}
            className="h-full w-full min-w-0 bg-transparent pr-9 pl-9 text-[16px] outline-none placeholder:text-ig-secondary [&::-webkit-search-cancel-button]:appearance-none"
            data-testid="search-input"
          />
          {value ? (
            <button
              type="button"
              // mousedown で blur される前に処理する（キャンセル表示が消えないように）
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onChange("")}
              aria-label="検索語を消去"
              className="absolute right-1.5 flex size-7 items-center justify-center"
            >
              <ClearIcon />
            </button>
          ) : null}
        </label>
        {showCancel ? (
          <button
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onClick={onCancel}
            className="shrink-0 text-[16px] pressable"
          >
            キャンセル
          </button>
        ) : null}
      </form>
    </div>
  );
});

/** iOS 風の「×」入りのグレーの丸 */
function ClearIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="8" className="fill-ig-secondary" />
      <path
        d="M5.3 5.3 10.7 10.7M10.7 5.3 5.3 10.7"
        className="stroke-ig-elevated"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}
