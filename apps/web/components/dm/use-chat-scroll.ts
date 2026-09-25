"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** 最下部からこの距離以内なら「最下部にいる」とみなす（新着で自動スクロールする） */
const NEAR_BOTTOM_PX = 150;
/** 位置補正の基準にする行（data-row-key を持つ行。上から順に並んでいる） */
const ROW_SELECTOR = "[data-row-key]";
/** 基準行の候補数（先頭の候補が消えたら次の候補で補正する） */
const ANCHOR_CANDIDATES = 8;

/** 位置補正の基準行（top は document 座標） */
export interface ScrollAnchor {
  key: string;
  top: number;
}

/**
 * 上から順に並んだ行のうち、下端が viewportTop より下にある（= 表示中か、それより下の）最初の行の添字。
 * 無ければ count。行の位置は単調なので二分探索する（行数が多くても計測は数回で済む）。
 */
export function findFirstVisibleRow(
  count: number,
  bottomAt: (index: number) => number,
  viewportTop: number,
): number {
  let low = 0;
  let high = count;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (bottomAt(mid) > viewportTop) high = mid;
    else low = mid + 1;
  }
  return low;
}

/**
 * 記録した基準行のうち、今も残っている最初の行が document 上でどれだけ動いたか（px）。
 * 1 行も残っていなければ null（補正しない）。
 */
export function anchorShift(
  anchors: readonly ScrollAnchor[],
  currentTop: (key: string) => number | null,
): number | null {
  for (const anchor of anchors) {
    const top = currentTop(anchor.key);
    if (top !== null) return top - anchor.top;
  }
  return null;
}

/**
 * 基準行の移動（shift）に対して実際にスクロールする量。
 * 上側の行が消えて文書が縮み、最下部付近にいたためにブラウザがスクロール位置を切り詰めた（clamp）場合は、
 * その分だけ既に見た目の位置が戻っているので差し引く（最下部にいたなら最下部のまま）。
 */
export function correctionFor(
  shift: number,
  scrollY: { captured: number; current: number; max: number },
): number {
  const clamped =
    scrollY.current >= scrollY.max - 1 ? Math.max(0, scrollY.captured - scrollY.current) : 0;
  return shift + clamped;
}

export interface ChatScrollOptions {
  /** 最初のページが表示された */
  ready: boolean;
  /** 一番新しい行の key（新着の検知） */
  newestKey: string | null;
  /** 一番新しい行が自分の発言（自分が送ったら常に最下部へ） */
  newestIsOwn: boolean;
  /** 入力中インジケーターの表示 */
  typing: boolean;
  /**
   * 末尾の見た目が変わったことを表す任意の文字列（送信失敗の表示・「既読」・メモリ通知など）。
   * 変わったとき最下部付近にいれば最下部を保つ
   */
  tailSignature?: string;
  /** 固定フッターの高さ（変わったら最下部を保つ） */
  bottomInset: number;
}

export interface ChatScroll {
  /** 最下部にいないときに新着が届いた → 「新しいメッセージ」 */
  hasUnseenNew: boolean;
  scrollToBottom: (behavior?: ScrollBehavior) => void;
  /** 初回の最下部スクロールが済んだ（過去ログの自動読み込みはこれ以降に有効化する） */
  initialScrollDone: boolean;
}

function documentHeight(): number {
  return document.documentElement.scrollHeight;
}

function isNearBottom(): boolean {
  return window.innerHeight + window.scrollY >= documentHeight() - NEAR_BOTTOM_PX;
}

function rowElement(key: string): HTMLElement | null {
  const selector = `[data-row-key="${typeof CSS !== "undefined" && CSS.escape ? CSS.escape(key) : key}"]`;
  return document.querySelector<HTMLElement>(selector);
}

function documentTop(element: HTMLElement): number {
  return element.getBoundingClientRect().top + window.scrollY;
}

/** 表示中の最初の行から ANCHOR_CANDIDATES 行を基準行として記録する（行が無ければ空） */
function captureAnchors(): ScrollAnchor[] {
  const rows = document.querySelectorAll<HTMLElement>(ROW_SELECTOR);
  if (rows.length === 0) return [];
  const scrollY = window.scrollY;
  const first = findFirstVisibleRow(
    rows.length,
    (index) => rows[index]!.getBoundingClientRect().bottom + scrollY,
    scrollY,
  );
  // すべて画面より上（末尾の余白を見ている等）なら末尾の行を使う
  const start = first < rows.length ? first : Math.max(0, rows.length - ANCHOR_CANDIDATES);
  const anchors: ScrollAnchor[] = [];
  for (let index = start; index < rows.length && anchors.length < ANCHOR_CANDIDATES; index += 1) {
    const row = rows[index]!;
    const key = row.dataset.rowKey;
    if (key) anchors.push({ key, top: row.getBoundingClientRect().top + scrollY });
  }
  return anchors;
}

/**
 * DM 会話のスクロール制御（ページ = ウィンドウのスクロールを使う）。
 * - 開いたとき最下部へ
 * - 見ている位置より上の高さが変わったとき（過去ログの追加・読み込み中表示・再取得で古い行が減った等）は、
 *   見ている位置がずれないよう補正する。基準は「表示中の最初の行」（一番古い行は再取得で消えることがある）
 *   （ブラウザ標準の scroll anchoring は Safari に無く挙動がそろわないため無効化して自前で行う）
 * - 新着: 最下部付近にいれば追従、そうでなければ hasUnseenNew
 */
export function useChatScroll({
  ready,
  newestKey,
  newestIsOwn,
  typing,
  bottomInset,
  tailSignature = "",
}: ChatScrollOptions): ChatScroll {
  const [hasUnseenNew, setHasUnseenNew] = useState(false);
  const [initialScrollDone, setInitialScrollDone] = useState(false);
  const initialDoneRef = useRef(false);
  const nearBottomRef = useRef(true);
  const autoScrollUntilRef = useRef(0);
  const anchorsRef = useRef<ScrollAnchor[]>([]);
  /** 基準行を記録した時点の scrollY */
  const anchorScrollYRef = useRef(0);
  const previous = useRef<{
    newestKey: string | null;
    typing: boolean;
    bottomInset: number;
    tailSignature: string;
  } | null>(null);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    nearBottomRef.current = true;
    autoScrollUntilRef.current = Date.now() + (behavior === "smooth" ? 600 : 100);
    window.scrollTo({ top: documentHeight(), behavior });
    setHasUnseenNew(false);
  }, []);

  // ブラウザの scroll anchoring を無効化（自前の補正と二重にならないように）
  useEffect(() => {
    const root = document.documentElement;
    const previousValue = root.style.overflowAnchor;
    root.style.overflowAnchor = "none";
    return () => {
      root.style.overflowAnchor = previousValue;
    };
  }, []);

  const recordAnchors = useCallback(() => {
    anchorsRef.current = captureAnchors();
    anchorScrollYRef.current = window.scrollY;
  }, []);

  useEffect(() => {
    const onScroll = () => {
      // 基準行は描画のたびに加えてスクロールでも取り直す（次の描画の直前の表示位置を基準にする）
      if (initialDoneRef.current) recordAnchors();
      const near = isNearBottom();
      // 自動スクロール中（smooth）の途中経過では「最下部から離れた」と判定しない
      if (!near && Date.now() < autoScrollUntilRef.current) return;
      nearBottomRef.current = near;
      if (near) setHasUnseenNew(false);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [recordAnchors]);

  // (a) 描画のたびに実行: 初回の最下部スクロールと、見ている位置より上の高さ変化
  //     （過去ログ追加・読み込み中表示・再取得で古い行が減った）の補正。
  //     補正は原因となる状態を問わず行うため依存配列なし（state は更新しない）
  useLayoutEffect(() => {
    if (!ready) return;
    if (!initialDoneRef.current) {
      initialDoneRef.current = true;
      scrollToBottom("auto");
    } else {
      const shift = anchorShift(anchorsRef.current, (key) => {
        const element = rowElement(key);
        return element ? documentTop(element) : null;
      });
      if (shift !== null && Math.abs(shift) >= 1) {
        const by = correctionFor(shift, {
          captured: anchorScrollYRef.current,
          current: window.scrollY,
          max: documentHeight() - window.innerHeight,
        });
        if (Math.abs(by) >= 1) window.scrollBy(0, by);
      }
    }
    recordAnchors();
  });

  // (b) 末尾側の変化: 新着・入力中表示・末尾の見た目・入力欄の高さ
  useLayoutEffect(() => {
    if (!ready) return;
    const prev = previous.current;
    previous.current = { newestKey, typing, bottomInset, tailSignature };
    if (prev === null) return; // 初回は (a) で最下部へスクロール済み

    const newArrived = newestKey !== null && newestKey !== prev.newestKey;
    if (newArrived) {
      if (newestIsOwn || nearBottomRef.current) scrollToBottom("smooth");
      else setHasUnseenNew(true);
    } else if (typing && !prev.typing && nearBottomRef.current) {
      scrollToBottom("smooth");
    } else if (tailSignature !== prev.tailSignature && nearBottomRef.current) {
      scrollToBottom("smooth");
    } else if (bottomInset !== prev.bottomInset && nearBottomRef.current) {
      scrollToBottom("auto");
    }
  }, [ready, newestKey, newestIsOwn, typing, bottomInset, tailSignature, scrollToBottom]);

  useEffect(() => {
    if (ready) setInitialScrollDone(true);
  }, [ready]);

  return { hasUnseenNew, scrollToBottom, initialScrollDone };
}
