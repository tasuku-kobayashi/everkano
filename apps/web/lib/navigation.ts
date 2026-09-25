import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

/**
 * アプリ内の戻る操作。
 * 直接 URL を開いた（= アプリ内の履歴が無い）場合に router.back() するとアプリ外へ出てしまうため、
 * アプリ内で何回画面遷移したかを数えておき、履歴が無ければフォールバック先へ replace する。
 */

let depth = 0;
let skipNextChange = false;

/** ルート（app/providers.tsx）で 1 回だけ呼ぶ */
export function useNavigationDepthTracker(): void {
  const pathname = usePathname();
  const first = useRef(true);

  useEffect(() => {
    const onPopState = () => {
      depth = Math.max(0, depth - 1);
      skipNextChange = true;
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (skipNextChange) {
      skipNextChange = false;
      return;
    }
    depth += 1;
  }, [pathname]);
}

/** アプリ内に戻れる履歴があるか */
export function canGoBackInApp(): boolean {
  return depth > 0;
}

/** 戻るボタンのハンドラ。履歴が無ければ fallbackHref（既定 "/"）へ */
export function useBackNavigation(fallbackHref = "/"): () => void {
  const router = useRouter();
  return useCallback(() => {
    if (canGoBackInApp()) {
      router.back();
    } else {
      router.replace(fallbackHref);
    }
  }, [router, fallbackHref]);
}
