"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { isScrolledToTop, refreshFeed, useFeedRefreshing } from "@/lib/feed-refresh";
import { Spinner } from "./spinner";

/** これ以上引っ張って離したら更新する（指の移動量 × 抵抗 0.5 後の px） */
export const PULL_THRESHOLD_PX = 64;
/** インジケーターの最大移動量（px） */
const PULL_MAX_PX = 96;
/** 指の移動量に掛ける抵抗 */
const PULL_RESISTANCE = 0.5;
/** 縦/横の判定を始める移動量（px） */
const DECIDE_AFTER_PX = 8;
/** この時間以上バックグラウンドにいたら、復帰時（先頭表示中なら）フィードを読み込み直す */
export const RESUME_REFRESH_AFTER_MS = 5 * 60_000;

/**
 * ホームフィードの再読み込み（MainShell が / のときだけ描画する）。
 *
 * 1. プルリフレッシュ: 先頭までスクロールした状態で下に引っ張って離すと再読み込み。
 *    body の overscroll-behavior-y: none（globals.css）でブラウザ標準のプルリフレッシュ（ページ全体の
 *    再読み込み）は無効化されているため、ホーム画面に追加した PWA でもこの独自実装で更新できる。
 * 2. 長時間（5 分以上）バックグラウンドにいたアプリに戻ったとき、先頭を表示していれば再読み込み。
 * 3. Home タブ / ロゴの再タップ（tab-bar.tsx / app-header.tsx）による再読み込み中もインジケーターを出す。
 *
 * タッチイベントは passive で購読し、スクロールそのものは妨げない。シート・モーダル表示中
 * （body のスクロールロック中）・ピンチズーム中・横スワイプ（ストーリーズ行）では反応しない。
 */
export function HomeFeedRefresher() {
  const queryClient = useQueryClient();
  const refreshing = useFeedRefreshing();
  const [pull, setPull] = useState(0);
  const pullRef = useRef(0);

  useEffect(() => {
    let start: { x: number; y: number } | null = null;
    let decided = false;

    const setPullDistance = (value: number) => {
      pullRef.current = value;
      setPull(value);
    };
    const reset = () => {
      start = null;
      decided = false;
      if (pullRef.current !== 0) setPullDistance(0);
    };

    const onTouchStart = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (
        event.touches.length !== 1 ||
        !touch ||
        !isScrolledToTop() ||
        document.body.style.overflow === "hidden" ||
        (window.visualViewport?.scale ?? 1) > 1.01
      ) {
        reset();
        return;
      }
      start = { x: touch.clientX, y: touch.clientY };
      decided = false;
    };

    const onTouchMove = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (!start || !touch || event.touches.length !== 1) {
        reset();
        return;
      }
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (!decided) {
        if (Math.abs(dx) < DECIDE_AFTER_PX && Math.abs(dy) < DECIDE_AFTER_PX) return;
        // 横スワイプ・上方向へのスクロールはプルリフレッシュではない
        if (Math.abs(dx) > Math.abs(dy) || dy < 0) {
          reset();
          return;
        }
        decided = true;
      }
      if (!isScrolledToTop()) {
        reset();
        return;
      }
      setPullDistance(Math.min(PULL_MAX_PX, Math.max(0, dy * PULL_RESISTANCE)));
    };

    const onTouchEnd = () => {
      const shouldRefresh = start !== null && pullRef.current >= PULL_THRESHOLD_PX;
      reset();
      if (shouldRefresh) void refreshFeed(queryClient);
    };

    const onTouchCancel = () => reset();

    window.addEventListener("touchstart", onTouchStart, { passive: true });
    window.addEventListener("touchmove", onTouchMove, { passive: true });
    window.addEventListener("touchend", onTouchEnd, { passive: true });
    window.addEventListener("touchcancel", onTouchCancel, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("touchend", onTouchEnd);
      window.removeEventListener("touchcancel", onTouchCancel);
    };
  }, [queryClient]);

  useEffect(() => {
    let hiddenAt: number | null = document.visibilityState === "hidden" ? Date.now() : null;
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt = Date.now();
        return;
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt;
      hiddenAt = null;
      // 途中までスクロールして読んでいる場合は、読んでいる位置を崩さない（Home タブで更新できる）
      if (away >= RESUME_REFRESH_AFTER_MS && isScrolledToTop()) {
        void refreshFeed(queryClient);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [queryClient]);

  const visible = refreshing || pull > 0;
  const offset = refreshing ? 12 : pull - 36;
  const progress = refreshing ? 1 : Math.min(1, pull / PULL_THRESHOLD_PX);

  return (
    <div
      className="pointer-events-none fixed inset-x-0 top-header z-20 mx-auto flex h-0 max-w-[480px] justify-center"
      data-testid="feed-refresh-indicator"
      data-state={
        refreshing
          ? "refreshing"
          : pull >= PULL_THRESHOLD_PX
            ? "armed"
            : pull > 0
              ? "pulling"
              : "idle"
      }
    >
      {visible ? (
        <span
          aria-hidden={refreshing ? undefined : true}
          className="flex size-9 items-center justify-center rounded-full bg-ig-bg text-ig-secondary shadow-[0_1px_6px_rgb(0_0_0/0.18)]"
          style={{
            transform: `translateY(${offset}px) scale(${0.6 + progress * 0.4})`,
            opacity: progress,
          }}
        >
          <Spinner size={20} label="フィードを更新中" />
        </span>
      ) : null}
    </div>
  );
}
