import type { InfiniteData, QueryClient } from "@tanstack/react-query";
import { useLayoutEffect } from "react";
import { queryKeys } from "@/lib/queries/keys";

/**
 * ホーム（/）のスクロール位置を覚えておき、タブバーの Home タブで戻ってきたときに元の位置へ戻す
 * （Instagram と同じ。検索・DM などを少し見てから戻っても、読んでいたところから続けられる）。
 *
 * - ブラウザの「戻る」（popstate）は Next.js / ブラウザが位置を復元するので扱わない。
 * - Home タブでの遷移は通常の push 遷移（新しい画面として先頭から表示される）なので、タブバーが遷移の前に
 *   requestHomeScrollRestore() を呼び、ホームが描画されたら useHomeScrollMemory() が元の位置へ戻す。
 * - フィードのキャッシュ（React Query）が残っていないとき（gcTime 経過後など）は復元しない
 *   （読み込み直したフィードでは同じ位置に同じ投稿は無い）。
 * - ホームで Home タブを再タップしたとき（先頭へ戻る・再読み込み）は、そのスクロールで位置が 0 に更新される。
 * - 復元待ちのあいだにユーザーが触ったら（タッチ・ホイール・キー操作）、その操作を優先して復元をやめる。
 */

/** これ以下のスクロール量は先頭扱い（復元しない） */
const TOP_TOLERANCE_PX = 2;
/** 復元をあきらめるまでの時間（ms）。画面の取得・フィードの描画を待つ */
const RESTORE_TIMEOUT_MS = 3_000;
/** 復元した位置が何フレーム続けて保たれたら完了とするか（直後に先頭へ戻されたらやり直す） */
const SETTLE_FRAMES = 3;
/** 画面遷移中のスケルトン（app/(main)/loading.tsx）の印。表示中はフィードがまだ無いので復元しない */
const ROUTE_LOADING_ATTRIBUTE = "data-route-loading";

let savedScrollY = 0;
let pendingScrollY: number | null = null;

/** ホームフィードの読み込み済みページがキャッシュに残っているか */
export function hasCachedFeedPages(queryClient: QueryClient): boolean {
  const data = queryClient.getQueryData<InfiniteData<unknown, unknown>>(queryKeys.feed());
  return Array.isArray(data?.pages) && data.pages.length > 0;
}

/**
 * タブバーの Home タブで、ホーム以外の画面からホームへ移るときに（遷移の前に）呼ぶ。
 * 覚えている位置があり、フィードのキャッシュが残っていれば、ホームの表示後にその位置へ戻す。
 */
export function requestHomeScrollRestore(queryClient: QueryClient): void {
  pendingScrollY =
    savedScrollY > TOP_TOLERANCE_PX && hasCachedFeedPages(queryClient) ? savedScrollY : null;
}

/**
 * ホーム表示中はスクロール位置を記録し、ホームに来たときに保留中の復元を行う（MainShell で使う）。
 * useLayoutEffect なので、ホームへの遷移を反映した描画の前（Next.js の遷移時のスクロール処理の後）に実行される。
 * フィードがキャッシュから同期的に描画されていれば、先頭が一瞬見えることなくその位置で表示される。
 */
export function useHomeScrollMemory(pathname: string): void {
  useLayoutEffect(() => {
    if (pathname !== "/") {
      // Home タブを押したが別の画面へ遷移した（続けて別のタブを押した等）: 後でホームへ来たときに勝手に動かさない
      pendingScrollY = null;
      return;
    }
    // 画面を離れるときのスクロール（遷移先の先頭表示・ページが短くなったことによる補正）は、
    // cleanup でリスナーを外した後に届くので記録されない
    const onScroll = () => {
      savedScrollY = window.scrollY;
    };
    window.addEventListener("scroll", onScroll, { passive: true });

    // 保留中の復元は、復元が終わる（位置が保たれた・時間切れ・ユーザーの操作）まで残す。
    // 開発時の StrictMode で effect が作り直されても、2 回目の実行で続きから復元できるようにするため
    const target = pendingScrollY;
    const cancelRestore =
      target === null
        ? undefined
        : restoreScrollPosition(target, () => {
            if (pendingScrollY === target) pendingScrollY = null;
          });

    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelRestore?.();
    };
  }, [pathname]);
}

/**
 * target までスクロールできる状態になったら（フィードが描画されたら）その位置へ戻す。
 * 復元が終わったら（位置が保たれた・時間切れ・ユーザーの操作）onFinish を呼ぶ。戻り値で中止できる（onFinish は呼ばない）。
 */
function restoreScrollPosition(target: number, onFinish: () => void): () => void {
  const startedAt = performance.now();
  let frame = 0;
  let settledFrames = 0;
  let applied = false;
  let stopped = false;

  const userEvents = ["touchstart", "wheel", "keydown", "pointerdown"] as const;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(frame);
    for (const type of userEvents) window.removeEventListener(type, finish, true);
  };
  const finish = () => {
    if (stopped) return;
    stop();
    onFinish();
  };

  const step = () => {
    if (stopped) return;
    const reachable =
      !document.querySelector(`[${ROUTE_LOADING_ATTRIBUTE}]`) &&
      document.documentElement.scrollHeight - window.innerHeight >= target - 1;
    if (reachable) {
      const current = window.scrollY;
      if (Math.abs(current - target) <= 1) {
        if (++settledFrames >= SETTLE_FRAMES) {
          finish();
          return;
        }
      } else if (!applied || current <= TOP_TOLERANCE_PX) {
        // まだ戻していない、または戻した直後に先頭へ戻された（画面遷移のスクロール処理）: 戻し直す
        window.scrollTo({ top: target, behavior: "instant" });
        applied = true;
        settledFrames = 0;
      } else {
        // 戻した後に別の位置へ動いた（ユーザー・アプリによるスクロール）: そちらを優先する
        finish();
        return;
      }
    }
    if (performance.now() - startedAt > RESTORE_TIMEOUT_MS) {
      finish();
      return;
    }
    frame = requestAnimationFrame(step);
  };

  for (const type of userEvents) {
    window.addEventListener(type, finish, { capture: true, passive: true });
  }
  step();
  return stop;
}
