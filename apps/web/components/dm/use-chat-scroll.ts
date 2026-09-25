"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** 最下部からこの距離以内なら「最下部にいる」とみなす（新着で自動スクロールする） */
const NEAR_BOTTOM_PX = 150;

export interface ChatScrollOptions {
  /** 最初のページが表示された */
  ready: boolean;
  /** 一番古い行の key（過去ログを先頭に足したときの位置補正の基準） */
  oldestKey: string | null;
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

/**
 * DM 会話のスクロール制御（ページ = ウィンドウのスクロールを使う）。
 * - 開いたとき最下部へ
 * - 過去ログを先頭に足したとき・読み込み中表示が出たときは、見ている位置がずれないよう補正する
 *   （ブラウザ標準の scroll anchoring は Safari に無く挙動がそろわないため無効化して自前で行う）
 * - 新着: 最下部付近にいれば追従、そうでなければ hasUnseenNew
 */
export function useChatScroll({
  ready,
  oldestKey,
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
  const anchorRef = useRef<{ key: string; top: number } | null>(null);
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

  useEffect(() => {
    const onScroll = () => {
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
  }, []);

  // (a) 描画のたびに実行: 初回の最下部スクロールと、先頭側の高さ変化（過去ログ追加・読み込み中表示）の補正。
  //     補正は原因となる状態を問わず行うため依存配列なし（state は更新しない）
  useLayoutEffect(() => {
    if (!ready) return;
    if (!initialDoneRef.current) {
      initialDoneRef.current = true;
      scrollToBottom("auto");
    } else {
      const anchor = anchorRef.current;
      const element = anchor ? rowElement(anchor.key) : null;
      if (anchor && element) {
        const delta = documentTop(element) - anchor.top;
        if (Math.abs(delta) >= 1) window.scrollBy(0, delta);
      }
    }
    const oldest = oldestKey ? rowElement(oldestKey) : null;
    if (oldest && oldestKey) anchorRef.current = { key: oldestKey, top: documentTop(oldest) };
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
