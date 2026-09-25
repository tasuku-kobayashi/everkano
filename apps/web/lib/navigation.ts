import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

/**
 * アプリ内の戻る操作。
 * 直接 URL を開いた（= アプリ内の履歴が無い）場合に router.back() するとアプリ外へ出てしまうため、
 * アプリ内で何回画面遷移したかを数えておき、履歴が無ければフォールバック先へ replace する。
 *
 * 数えるのは「履歴が 1 つ増える遷移」（Link / router.push）だけ。
 * - ブラウザの戻る/進む（popstate）は 1 つ減らし、その画面変化は数えない
 * - router.replace は履歴が増えないため、呼ぶ前に markReplaceNavigation() で次の画面変化を数えないようにする
 *   （数えてしまうと、履歴が無いのに router.back() してアプリ外へ出てしまう）
 * - ログイン直後などのハードナビゲーション（window.location）はページごと読み込み直すため 0 に戻る
 * 数え漏らし（少なく数える）はフォールバック先へ移動するだけで安全、多く数えるとアプリ外へ出てしまう。
 */

let depth = 0;
let skipNextChange = false;

/** ブラウザの戻る/進む（popstate）を記録する */
export function recordPopState(): void {
  depth = Math.max(0, depth - 1);
  skipNextChange = true;
}

/** 画面（pathname）が変わったことを記録する（初回表示は呼ばない） */
export function recordPathnameChange(): void {
  if (skipNextChange) {
    skipNextChange = false;
    return;
  }
  depth += 1;
}

/**
 * router.replace の直前に呼ぶ。次の画面変化を「履歴が増えた遷移」として数えない。
 * 同じ pathname への replace（画面変化が起きない）で呼んだ場合は次の遷移が数えられないが、
 * 少なく数えるのは安全側（戻るボタンがフォールバック先へ移動するだけ）。
 */
export function markReplaceNavigation(): void {
  skipNextChange = true;
}

/** アプリ内に戻れる履歴があるか */
export function canGoBackInApp(): boolean {
  return depth > 0;
}

/** ルート（app/providers.tsx）で 1 回だけ呼ぶ */
export function useNavigationDepthTracker(): void {
  const pathname = usePathname();
  const first = useRef(true);

  useEffect(() => {
    window.addEventListener("popstate", recordPopState);
    return () => window.removeEventListener("popstate", recordPopState);
  }, []);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    recordPathnameChange();
  }, [pathname]);
}

/** 戻るボタンのハンドラ。履歴が無ければ fallbackHref（既定 "/"）へ */
export function useBackNavigation(fallbackHref = "/"): () => void {
  const router = useRouter();
  return useCallback(() => {
    if (canGoBackInApp()) {
      router.back();
    } else {
      markReplaceNavigation();
      router.replace(fallbackHref);
    }
  }, [router, fallbackHref]);
}
