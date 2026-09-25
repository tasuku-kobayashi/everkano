import { useEffect, useId, useRef, useState, type RefObject } from "react";
import { registerOverlayHistoryEntry } from "@/lib/navigation";

/**
 * モーダル / ボトムシート共通のユーティリティ。
 */

/** フォーカス可能な要素のセレクター（フォーカストラップ・初期フォーカスの決定に使う） */
export const FOCUSABLE_SELECTOR =
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

export interface FocusTrapOptions {
  /**
   * 開いたときに最初にフォーカスする要素を返す。null を返すとコンテナ自体（tabIndex=-1）にフォーカスする。
   * 省略時は最初のフォーカス可能要素。
   * 取り消せない操作の確認ダイアログでは、破壊的なボタンに初期フォーカスを当てないために使う
   * （WAI-ARIA APG の alertdialog: 最も破壊的でない操作にフォーカスする）。
   */
  initialFocus?: (container: HTMLElement) => HTMLElement | null;
}

/**
 * 簡易フォーカストラップ:
 * - 開いたときに最初のフォーカス可能要素（options.initialFocus で変更可。無ければコンテナ）へフォーカス
 * - Tab / Shift+Tab をコンテナ内で循環、Esc で onEscape
 * - 閉じたら元の要素にフォーカスを戻す
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  onEscape: () => void,
  options: FocusTrapOptions = {},
): void {
  const onEscapeRef = useRef(onEscape);
  const initialFocusRef = useRef(options.initialFocus);
  useEffect(() => {
    onEscapeRef.current = onEscape;
    initialFocusRef.current = options.initialFocus;
  }, [onEscape, options.initialFocus]);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );

    const initialFocus = initialFocusRef.current;
    const first = initialFocus ? initialFocus(container) : focusables()[0];
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
 * 端末の「戻る」（Android の戻るジェスチャー・ブラウザの戻る）でオーバーレイを閉じる。
 * 開いている間だけ同じ URL の履歴エントリを 1 つ積み、「戻る」でそれが外れたら onDismiss を呼ぶ。
 * UI で閉じたとき（open が false になった・アンマウント）は積んだエントリを取り除く（戻る 1 回分が無駄にならない）。
 * 仕組みと Next.js ルーターとの関係は lib/navigation.ts の registerOverlayHistoryEntry を参照。
 */
export function useHistoryDismiss(open: boolean, onDismiss: () => void): void {
  const id = useId();
  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (!open) return;
    return registerOverlayHistoryEntry(id, () => onDismissRef.current());
  }, [open, id]);
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
