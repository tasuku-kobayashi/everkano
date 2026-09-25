"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { prefetchCharacterProfile } from "@/lib/queries/prefetch";
import {
  estimateCaptionCut,
  findCaptionCut,
  graphemeBoundaries,
  scheduleAfterPaint,
} from "./caption-measure";
import { RichText } from "./rich-text";

/** 折りたたみ時の最大行数（Instagram のフィードは 2 行） */
const MAX_LINES = 2;
const MORE_LABEL = "続きを読む";

/**
 * キャプション（先頭に太字の handle）。
 *
 * clamp=true のときは Instagram と同じく「2 行に収まる所まで本文 + … 続きを読む」をインラインで表示する。
 * 収まる文字数は非表示の計測用要素で求める（幅が変わったら再計算）。タップで全文。
 * - 計測は描画後のアイドル時に行う（それまでは line-clamp で 2 行に収める）
 * - 全文を 1 回レイアウトして行の位置から省略位置を見積もり、その前後だけ確かめる
 *   （1 文字ずつの二分探索より強制レイアウトが大幅に少ない。見積もれないときは二分探索）
 */
export function PostCaption({
  handle,
  caption,
  clamp,
}: {
  handle: string;
  caption: string;
  clamp: boolean;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  /** 折りたたみ時に表示する文字数（UTF-16 のオフセット）。null = 全文が収まる（または未計測） */
  const [cut, setCut] = useState<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLParagraphElement>(null);
  const measureTextRef = useRef<HTMLSpanElement>(null);
  const measureMoreRef = useRef<HTMLSpanElement>(null);
  const measureMoreWidthRef = useRef<HTMLSpanElement>(null);
  const collapsed = clamp && !expanded;

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const box = measureRef.current;
    const text = measureTextRef.current;
    const more = measureMoreRef.current;
    const moreWidthProbe = measureMoreWidthRef.current;
    if (!collapsed || !wrapper || !box || !text || !more || !moreWidthProbe) return;

    let lastWidth = -1;
    const compute = () => {
      const width = wrapper.clientWidth;
      if (width === lastWidth || width === 0) return;
      lastWidth = width;
      const lineHeight = Number.parseFloat(getComputedStyle(box).lineHeight) || 18;
      const maxHeight = lineHeight * MAX_LINES + 1;
      const fits = () => box.getBoundingClientRect().height <= maxHeight;

      // 全文が収まるなら省略しない
      more.style.display = "none";
      text.textContent = caption;
      if (fits()) {
        setCut(null);
        return;
      }

      // 全文のレイアウトから省略位置を見積もる（読み取りのみ。DOM を書き換える前に行う）
      const bounds = graphemeBoundaries(caption);
      const textNode = text.firstChild;
      const estimate =
        textNode instanceof Text
          ? estimateCaptionCut(textNode, bounds, {
              box: box.getBoundingClientRect(),
              lineHeight,
              maxLines: MAX_LINES,
              moreWidth: moreWidthProbe.getBoundingClientRect().width,
            })
          : null;

      more.style.display = "";
      // 全文は収まらないので、答えは 0〜(書記素数 - 1)。先頭 i 書記素 + 「… 続きを読む」が 2 行に収まるか
      const count = findCaptionCut(bounds.length - 2, estimate, (i) => {
        text.textContent = caption.slice(0, bounds[i] ?? 0).trimEnd();
        return fits();
      });
      setCut(bounds[count] ?? 0);
    };

    let cancel: (() => void) | null = null;
    const schedule = () => {
      cancel?.();
      cancel = scheduleAfterPaint(() => {
        cancel = null;
        compute();
      });
    };
    schedule();

    if (typeof ResizeObserver === "undefined") return () => cancel?.();
    let observedWidth = -1;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? -1;
      if (width === observedWidth) return; // 高さだけの変化（省略の適用など）では測り直さない
      observedWidth = width;
      schedule();
    });
    observer.observe(wrapper);
    return () => {
      cancel?.();
      observer.disconnect();
    };
  }, [collapsed, caption]);

  const truncated = collapsed && cut !== null;

  const content = (body: ReactNode, more: ReactNode, handleNode: ReactNode) => (
    <>
      {handleNode}
      {body}
      {more}
    </>
  );

  return (
    <div ref={wrapperRef} className="relative px-3">
      <p
        className={cn(
          "text-[14px] leading-[18px] text-wrap-anywhere whitespace-pre-line",
          // 計測前・計測できない環境でも 2 行を超えないように
          collapsed && "line-clamp-2",
        )}
        onClick={truncated ? () => setExpanded(true) : undefined}
      >
        {content(
          <RichText text={truncated ? caption.slice(0, cut).trimEnd() : caption} />,
          truncated ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="text-ig-secondary"
              aria-label="キャプションの続きを読む"
            >
              <span aria-hidden="true">… </span>
              {MORE_LABEL}
            </button>
          ) : null,
          <Link
            href={`/c/${handle}`}
            onPointerDown={() => prefetchCharacterProfile(queryClient, handle)}
            className="mr-1.5 font-semibold"
          >
            {handle}
          </Link>,
        )}
      </p>
      {collapsed ? (
        // 計測用（非表示・同じ幅と書式）
        <p
          ref={measureRef}
          aria-hidden="true"
          className="pointer-events-none invisible absolute inset-x-3 top-0 text-[14px] leading-[18px] text-wrap-anywhere whitespace-pre-line"
        >
          {content(
            <span ref={measureTextRef} />,
            <span ref={measureMoreRef}>… {MORE_LABEL}</span>,
            <span className="mr-1.5 font-semibold">{handle}</span>,
          )}
          {/* 「… 続きを読む」の幅（折り返さない状態。流れの外に置き、高さの計測に影響させない） */}
          <span ref={measureMoreWidthRef} className="absolute top-0 left-0 whitespace-nowrap">
            … {MORE_LABEL}
          </span>
        </p>
      ) : null}
    </div>
  );
}
