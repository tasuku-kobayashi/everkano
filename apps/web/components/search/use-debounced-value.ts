"use client";

import { useEffect, useState } from "react";

/** value の変化が delayMs 止まってから反映される値（検索の入力中に毎キー問い合わせないため） */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
