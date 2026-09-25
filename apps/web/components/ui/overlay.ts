import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * モーダル / ボトムシート共通のユーティリティ。
 */

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

let scrollLockCount = 0;
let savedOverflow = "";

/** 開いている間 body のスクロールを止める（入れ子に対応） */
export function useScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    if (scrollLockCount === 0) {
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    scrollLockCount += 1;
    return () => {
      scrollLockCount -= 1;
      if (scrollLockCount === 0) document.body.style.overflow = savedOverflow;
    };
  }, [active]);
}

/**
 * 簡易フォーカストラップ:
 * - 開いたときに最初のフォーカス可能要素（無ければコンテナ）へフォーカス
 * - Tab / Shift+Tab をコンテナ内で循環、Esc で onEscape
 * - 閉じたら元の要素にフォーカスを戻す
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  onEscape: () => void,
): void {
  const onEscapeRef = useRef(onEscape);
  useEffect(() => {
    onEscapeRef.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );

    const first = focusables()[0];
    (first ?? container).focus({ preventScroll: true });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onEscapeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }
      const firstItem = items[0]!;
      const lastItem = items[items.length - 1]!;
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    };

    container.addEventListener("keydown", onKeyDown);
    return () => {
      container.removeEventListener("keydown", onKeyDown);
      if (previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [active, containerRef]);
}

/**
 * 開閉アニメーション用の状態。
 * - mounted: DOM に存在するか（閉じるアニメーション中も true）
 * - visible: 表示状態のクラスを当てるか
 */
export function usePresence(open: boolean, exitMs = 220): { mounted: boolean; visible: boolean } {
  const [mounted, setMounted] = useState(open);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      // 次フレームで visible にして CSS トランジションを発火させる
      const raf = requestAnimationFrame(() => requestAnimationFrame(() => setVisible(true)));
      return () => cancelAnimationFrame(raf);
    }
    setVisible(false);
    const timer = setTimeout(() => setMounted(false), exitMs);
    return () => clearTimeout(timer);
  }, [open, exitMs]);

  return { mounted, visible };
}

/** クライアントでマウント済みか（createPortal を SSR で呼ばないため） */
export function useIsClient(): boolean {
  const [isClient, setIsClient] = useState(false);
  useEffect(() => setIsClient(true), []);
  return isClient;
}
