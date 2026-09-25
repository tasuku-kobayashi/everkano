"use client";

import Link from "next/link";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/** 折りたたみ時の最大行数（Instagram のフィードは 2 行） */
const MAX_LINES = 2;
const MORE_LABEL = "続きを読む";

/**
 * キャプション（先頭に太字の handle）。
 *
 * clamp=true のときは Instagram と同じく「2 行に収まる所まで本文 + … 続きを読む」をインラインで表示する。
 * 収まる文字数は非表示の計測用要素で二分探索して求める（幅が変わったら再計算）。タップで全文。
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
  const [expanded, setExpanded] = useState(false);
  /** 折りたたみ時に表示する文字数。null = 全文が収まる（または未計測） */
  const [cut, setCut] = useState<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLParagraphElement>(null);
  const measureTextRef = useRef<HTMLSpanElement>(null);
  const measureMoreRef = useRef<HTMLSpanElement>(null);
  const collapsed = clamp && !expanded;

  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;
    const box = measureRef.current;
    const text = measureTextRef.current;
    const more = measureMoreRef.current;
    if (!collapsed || !wrapper || !box || !text || !more) return;

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
      more.style.display = "";
      let lo = 0;
      let hi = caption.length;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        text.textContent = caption.slice(0, mid).trimEnd();
        if (fits()) lo = mid;
        else hi = mid - 1;
      }
      setCut(lo);
    };

    compute();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(compute);
    observer.observe(wrapper);
    return () => observer.disconnect();
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
          truncated ? caption.slice(0, cut).trimEnd() : caption,
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
          <Link href={`/c/${handle}`} className="mr-1.5 font-semibold">
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
        </p>
      ) : null}
    </div>
  );
}
