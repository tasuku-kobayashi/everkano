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
//
// history.go(-n) は非同期で、Chromium は戻る先を「呼んだ時点の」エントリから決める。着地する前に Next.js が
// 遷移のエントリを積む（pushState）と、着地先はその下（元の画面のエントリ）になり、遷移先のエントリは「進む」側に
// 取り残される。その後の Next.js の replaceState で元の画面のエントリが遷移先の URL に書き換わり、「戻る」で
// 元の画面を飛ばしてその前（ログイン直後ならマジックリンクの確認画面）まで戻ってしまっていた。
// そこで pushState / replaceState を包み（installHistoryGuard）、自分の history.go(-n) が着地するまでの履歴の
// 書き込み（Next.js の遷移・オーバーレイの追加）を保留して、着地後に順に適用する。履歴の操作が直列になるため、
// 取り除いたエントリの直下に必ず着地し、その後に遷移先のエントリが積まれる。
// また、オーバーレイを開いたまま画面遷移した場合（pushState）は、先にオーバーレイのエントリを取り除いてから
// 遷移のエントリを積む（同じ URL の不要なエントリを残さない。残すと「戻る」が 1 回空振りする）。

/** オーバーレイの履歴エントリの印（history.state のキー） */
export const OVERLAY_HISTORY_KEY = "__everkanoOverlay";

/** 自分の history.go(-n) の popstate が届かない場合に、保留した履歴の操作を適用するまでの時間（ms） */
const OWN_TRAVERSAL_FAILSAFE_MS = 3_000;

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
/** 自分で呼んだ history.go(-n) の popstate を待っている */
let ownTraversalPending = false;
let ownTraversalFailsafe: ReturnType<typeof setTimeout> | undefined;
let flushScheduled = false;
/** 自分の history.go(-n) の着地を待つ間、保留している履歴の操作（着地後に順に実行する） */
const deferredHistoryOps: (() => void)[] = [];
/** pushState / replaceState を包んだ History（1 回だけ包む） */
let guardedHistory: History | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function overlayIdOf(state: unknown): string | undefined {
  if (!isRecord(state)) return undefined;
  const id = state[OVERLAY_HISTORY_KEY];
  return typeof id === "string" ? id : undefined;
}

/** 開いている（積み終えた・閉じる予定でない）オーバーレイの id か */
function isLiveOverlayId(id: string | undefined): boolean {
  return id !== undefined && overlayEntries.some((e) => e.id === id && e.pushed && !e.closing);
}

/** 履歴の操作を実行する。自分の history.go(-n) の着地待ちなら、着地後まで保留する */
function runHistoryOp(op: () => void): void {
  if (ownTraversalPending) deferredHistoryOps.push(op);
  else op();
}

function drainDeferredHistoryOps(): void {
  // 保留していた操作が新たに history.go(-n) を呼んだら、残りはその着地まで待つ
  while (!ownTraversalPending && deferredHistoryOps.length > 0) deferredHistoryOps.shift()?.();
}

function startOwnTraversal(count: number): void {
  ownTraversalPending = true;
  clearTimeout(ownTraversalFailsafe);
  // 念のため: popstate が届かなかった場合も、保留した操作を適用し以降の「戻る」を取りこぼさない
  ownTraversalFailsafe = setTimeout(finishOwnTraversal, OWN_TRAVERSAL_FAILSAFE_MS);
  window.history.go(-count);
}

function finishOwnTraversal(): void {
  if (!ownTraversalPending) return;
  ownTraversalPending = false;
  clearTimeout(ownTraversalFailsafe);
  drainDeferredHistoryOps();
}

/**
 * 他のコード（Next.js のルーター）が書き込む state からオーバーレイの印を整える。
 * - 今いるオーバーレイのエントリを同じ URL のまま書き換える場合（ルーターの再描画の replaceState）は印を残す
 *   （消えると、UI で閉じたときに自分のエントリか判定できず、取り除けない）
 * - 閉じたオーバーレイの印（書き込みを保留していた間に元のエントリから写されたもの）は消す
 */
function reconcileOverlayMark(data: unknown, keepCurrent: boolean): unknown {
  const id = overlayIdOf(data);
  if (id !== undefined) {
    if (overlayEntries.some((e) => e.id === id)) return data;
    const rest: Record<string, unknown> = { ...(data as Record<string, unknown>) };
    delete rest[OVERLAY_HISTORY_KEY];
    return rest;
  }
  const currentId = overlayIdOf(window.history.state);
  if (keepCurrent && isLiveOverlayId(currentId) && isRecord(data)) {
    return { ...data, [OVERLAY_HISTORY_KEY]: currentId };
  }
  return data;
}

function isSameHref(url: string | URL | null | undefined): boolean {
  if (url === undefined || url === null) return true;
  try {
    return new URL(url, window.location.href).href === window.location.href;
  } catch {
    return false;
  }
}

/**
 * history.pushState / replaceState を包む（冪等）。Next.js の AppRouter も同じ関数を包むが、どちらが外側でも
 * すべての書き込みがここを通る（Next.js の包みは内部の state（__NA）付きの書き込みをそのまま渡す）。
 */
export function installHistoryGuard(): void {
  if (typeof window === "undefined") return;
  const history = window.history;
  if (guardedHistory === history) return;
  guardedHistory = history;
  const push = history.pushState;
  const replace = history.replaceState;

  // 画面遷移（オーバーレイ自身のエントリ以外の書き込みで URL が変わるもの）の前に、開いたままのオーバーレイの
  // エントリを取り除く。取り除く history.go(-n) の着地を待ってから、この書き込みを（保留中の他の操作より先に）行う
  const write = (
    apply: (data: unknown, unused: string, url?: string | URL | null) => void,
    isNavigation: (data: unknown, url?: string | URL | null) => boolean,
  ) =>
    function guardedWrite(data: unknown, unused: string, url?: string | URL | null): void {
      runHistoryOp(() => {
        const navigating = isNavigation(data, url);
        if (navigating) {
          leaveOverlaysForNavigation();
          if (ownTraversalPending) {
            deferredHistoryOps.unshift(() => apply(reconcileOverlayMark(data, false), unused, url));
            return;
          }
        }
        apply(reconcileOverlayMark(data, !navigating), unused, url);
      });
    };

  history.pushState = write(
    (data, unused, url) => push.call(history, data, unused, url),
    (data) => !isLiveOverlayId(overlayIdOf(data)),
  );
  history.replaceState = write(
    (data, unused, url) => replace.call(history, data, unused, url),
    (data, url) => !isSameHref(url) && !isLiveOverlayId(overlayIdOf(data)),
  );
}

/**
 * 画面遷移の直前（オーバーレイを開いたまま遷移した・閉じると同時に遷移した）: 今いる画面に積んだオーバーレイの
 * エントリを取り除く（遷移のエントリはその着地後に積まれる）。開いたままのオーバーレイは閉じる。
 */
function leaveOverlaysForNavigation(): void {
  const top = overlayEntries.at(-1);
  if (top?.pushed && !top.closing && overlayIdOf(window.history.state) === top.id) {
    for (const entry of overlayEntries) {
      if (!entry.pushed || entry.closing) continue;
      entry.closing = true;
      // React の描画中（Next.js の useInsertionEffect）に呼ばれるため、状態の更新は後で行う
      queueMicrotask(() => entry.onPop());
    }
  }
  flushClosingEntries();
}

/**
 * オーバーレイを開いたときに呼ぶ。端末の「戻る」で onPop が呼ばれる。
 * 戻り値の release() は、オーバーレイを UI で閉じたとき・アンマウント時に呼ぶ（冪等）。
 */
export function registerOverlayHistoryEntry(id: string, onPop: () => void): () => void {
  installHistoryGuard();
  const entry: OverlayEntry = {
    id,
    href: window.location.href,
    onPop,
    pushed: false,
    closing: false,
  };
  overlayEntries.push(entry);
  queueMicrotask(() => runHistoryOp(() => pushOverlayEntry(entry)));
  return () => releaseOverlayEntry(entry);
}

function pushOverlayEntry(entry: OverlayEntry): void {
  if (!overlayEntries.includes(entry) || entry.closing) return;
  if (window.location.href !== entry.href) {
    overlayEntries.splice(overlayEntries.indexOf(entry), 1);
    return;
  }
  // Next.js の履歴 state（__NA / ツリー）を引き継いだまま、同じ URL のエントリを積む
  const state: unknown = window.history.state;
  entry.pushed = true;
  window.history.pushState(
    { ...(isRecord(state) ? state : {}), [OVERLAY_HISTORY_KEY]: entry.id },
    "",
  );
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
    queueMicrotask(() => {
      flushScheduled = false;
      runHistoryOp(flushClosingEntries);
    });
  }
}

function flushClosingEntries(): void {
  let count = 0;
  let top: OverlayEntry | undefined;
  while (overlayEntries.at(-1)?.closing) {
    const entry = overlayEntries.pop();
    top ??= entry;
    count += 1;
  }
  // 上に別のオーバーレイが残っている途中のエントリは履歴から外せないため、登録だけ外す
  for (let i = overlayEntries.length - 1; i >= 0; i -= 1) {
    if (overlayEntries[i]?.closing) overlayEntries.splice(i, 1);
  }
  // 自分が積んだエントリの上にいるときだけ戻す。別のエントリにいる（「戻る」で既に外れた・state が
  // 置き換えられた）のに戻すと、アプリの前の画面やその前（ログイン画面）まで戻ってしまう
  if (!top || overlayIdOf(window.history.state) !== top.id) return;
  startOwnTraversal(count);
}

/**
 * popstate の処理（capture）。オーバーレイ由来のものは Next.js と depth カウントに渡さない。
 * テストのため export している。
 */
export function handlePopState(
  event: Pick<PopStateEvent, "state" | "stopImmediatePropagation">,
): void {
  if (ownTraversalPending) {
    // UI で閉じたオーバーレイのエントリを自分で取り除いた（同じ画面のまま）。着地までの履歴の書き込みは
    // 保留しているので、取り除いたエントリの直下に着地している。保留していた書き込み（遷移など）をここで適用する
    event.stopImmediatePropagation();
    finishOwnTraversal();
    return;
  }

  const stateId = overlayIdOf(event.state);
  const pushedEntries = overlayEntries.filter((entry) => entry.pushed);
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
    // Next.js（AppRouter）の effect より先に実行される（上のコメント参照）
    installHistoryGuard();
    // capture: Next.js（AppRouter）の popstate リスナーより先に処理する
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
