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

// ---------------------------------------------------------------------------
// オーバーレイ（ボトムシート・モーダル）と端末の「戻る」
// ---------------------------------------------------------------------------
//
// Android の戻るジェスチャー / ブラウザの戻るで、開いているシート・モーダルだけを閉じる（Instagram と同じ）。
// 開いたときに同じ URL の履歴エントリを 1 つ積み、「戻る」でそのエントリが外れたら閉じる。
// UI（背景タップ・Esc・キャンセル等）で閉じたときは、自分で積んだエントリを history.go(-n) で取り除く。
//
// これらのオーバーレイ由来の popstate は、Next.js のルーター（復元処理）と上の depth カウントには渡さない
// （stopImmediatePropagation）。渡すと、シートの操作と同時に始めた router.push（例: 「プロフィールを見る」）が
// 復元処理で破棄されたり、depth を少なく数えて戻るボタンがフォールバック先へ移動したりする。
// このリスナーは Providers（Next.js の AppRouter より内側 = 先に effect が実行される）で capture 登録するため、
// Next.js の popstate リスナーより先に呼ばれる。

/** オーバーレイの履歴エントリの印（history.state のキー） */
export const OVERLAY_HISTORY_KEY = "__everkanoOverlay";

interface OverlayEntry {
  id: string;
  /** エントリを積んだときの URL（その後に画面遷移したかの判定に使う） */
  href: string;
  onPop: () => void;
  /** history にエントリを積み終えたか（StrictMode の effect 二重実行で積まないよう、積むのは microtask で） */
  pushed: boolean;
  /** UI で閉じられ、history から取り除く予定 */
  closing: boolean;
}

const overlayEntries: OverlayEntry[] = [];
/** 自分で呼んだ history.go(-n) のうち、まだ popstate が届いていない数 */
let ownTraversals = 0;
let ownTraversalFailsafe: ReturnType<typeof setTimeout> | undefined;
let flushScheduled = false;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function overlayIdOf(state: unknown): string | undefined {
  if (!isRecord(state)) return undefined;
  const id = state[OVERLAY_HISTORY_KEY];
  return typeof id === "string" ? id : undefined;
}

/**
 * オーバーレイを開いたときに呼ぶ。端末の「戻る」で onPop が呼ばれる。
 * 戻り値の release() は、オーバーレイを UI で閉じたとき・アンマウント時に呼ぶ（冪等）。
 */
export function registerOverlayHistoryEntry(id: string, onPop: () => void): () => void {
  const entry: OverlayEntry = {
    id,
    href: window.location.href,
    onPop,
    pushed: false,
    closing: false,
  };
  overlayEntries.push(entry);
  queueMicrotask(() => {
    if (!overlayEntries.includes(entry) || entry.closing) return;
    if (window.location.href !== entry.href) {
      overlayEntries.splice(overlayEntries.indexOf(entry), 1);
      return;
    }
    // Next.js の履歴 state（__NA / ツリー）を引き継いだまま、同じ URL のエントリを積む
    const state: unknown = window.history.state;
    window.history.pushState({ ...(isRecord(state) ? state : {}), [OVERLAY_HISTORY_KEY]: id }, "");
    entry.pushed = true;
  });
  return () => releaseOverlayEntry(entry);
}

function releaseOverlayEntry(entry: OverlayEntry): void {
  const index = overlayEntries.indexOf(entry);
  if (index === -1 || entry.closing) return; // 「戻る」で閉じた後など
  if (!entry.pushed || window.location.href !== entry.href) {
    // まだ積んでいない / 画面遷移した（エントリは新しい画面の下に残る）→ 履歴は操作しない
    overlayEntries.splice(index, 1);
    return;
  }
  entry.closing = true;
  if (!flushScheduled) {
    flushScheduled = true;
    // 同じコミットで複数のオーバーレイが閉じた場合（シート + その上の確認ダイアログ）に 1 回で戻す
    queueMicrotask(flushClosingEntries);
  }
}

function flushClosingEntries(): void {
  flushScheduled = false;
  let count = 0;
  while (overlayEntries.at(-1)?.closing) {
    overlayEntries.pop();
    count += 1;
  }
  // 上に別のオーバーレイが残っている途中のエントリは履歴から外せないため、登録だけ外す
  for (let i = overlayEntries.length - 1; i >= 0; i -= 1) {
    if (overlayEntries[i]?.closing) overlayEntries.splice(i, 1);
  }
  if (count === 0) return;
  ownTraversals += 1;
  clearTimeout(ownTraversalFailsafe);
  // 念のため: popstate が届かなかった場合に以降の「戻る」を取りこぼさない
  ownTraversalFailsafe = setTimeout(() => {
    ownTraversals = 0;
  }, 2_000);
  window.history.go(-count);
}

/**
 * popstate の処理（capture）。オーバーレイ由来のものは Next.js と depth カウントに渡さない。
 * テストのため export している。
 */
export function handlePopState(
  event: Pick<PopStateEvent, "state" | "stopImmediatePropagation">,
): void {
  const stateId = overlayIdOf(event.state);
  const pushedEntries = overlayEntries.filter((entry) => entry.pushed);

  if (ownTraversals > 0) {
    ownTraversals -= 1;
    if (stateId === undefined || pushedEntries.some((entry) => entry.id === stateId)) {
      // UI で閉じたオーバーレイのエントリを自分で取り除いた（同じ画面のまま）
      event.stopImmediatePropagation();
      return;
    }
    // 取り除く前に別の遷移が積まれて古いオーバーレイのエントリに着地した → 通常の「戻る」として扱う
  }

  const top = pushedEntries.at(-1);
  if (top && stateId !== top.id) {
    // 「戻る」でオーバーレイのエントリが外れた → そのオーバーレイ（と、その上のもの）を閉じる
    const keepUntil =
      stateId === undefined ? -1 : overlayEntries.findIndex((e) => e.id === stateId);
    const popped = overlayEntries.splice(keepUntil + 1).filter((entry) => entry.pushed);
    const samePage = popped.every((entry) => entry.href === window.location.href);
    for (const entry of popped.reverse()) entry.onPop();
    if (samePage) {
      event.stopImmediatePropagation();
      return;
    }
    // 履歴メニュー等で別の画面まで一気に戻った → 通常の「戻る」として Next.js に処理させる
  }

  recordPopState();
}

/** ルート（app/providers.tsx）で 1 回だけ呼ぶ */
export function useNavigationDepthTracker(): void {
  const pathname = usePathname();
  const first = useRef(true);

  useEffect(() => {
    // capture: Next.js（AppRouter）の popstate リスナーより先に処理する（上のコメント参照）
    window.addEventListener("popstate", handlePopState, { capture: true });
    return () => window.removeEventListener("popstate", handlePopState, { capture: true });
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
