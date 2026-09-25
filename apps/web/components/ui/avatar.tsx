"use client";

import { useCallback, useState } from "react";
import { cn } from "@/lib/cn";
import { getStorageAdapter } from "@/lib/storage";
import { UserIcon } from "./icons";

export type AvatarSize = "xs" | "sm" | "md" | "lg" | "xl" | "2xl";

/** 画像部分の直径（px）。Instagram の各所の寸法に合わせている */
export const AVATAR_SIZES: Record<AvatarSize, number> = {
  xs: 24, // DM ヘッダー・小さなコメント
  sm: 32, // フィードの投稿ヘッダー・コメント
  md: 44, // 検索結果・メモリパネル
  lg: 56, // DM 一覧・ストーリーズ行
  xl: 64, // ストーリーズ行（大）
  "2xl": 86, // プロフィール上部
};

export type AvatarRing = "none" | "story" | "seen" | "active";

export interface AvatarProps {
  /** DB の avatar_url（絶対URL またはオブジェクトキー） */
  src?: string | null;
  /** 代替テキスト（キャラ名など）。画像が無い場合は頭文字を表示 */
  alt: string;
  size?: AvatarSize | number;
  /**
   * story: ストーリーズ風のグラデーションリング / seen: 既読のグレーリング /
   * active: タブバー等の選択中リング（テキスト色） / none: リングなし
   */
  ring?: AvatarRing;
  /** 画像が無い・読み込めない場合に表示する文字（既定: alt の 1 文字目） */
  fallbackText?: string;
  className?: string;
}

export function Avatar({
  src,
  alt,
  size = "sm",
  ring = "none",
  fallbackText,
  className,
}: AvatarProps) {
  const px = typeof size === "number" ? size : AVATAR_SIZES[size];
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  // 高解像度端末向けに 3 倍幅でリクエスト（CDN 変換対応時のみ効く）
  const resolved = src ? getStorageAdapter().resolveImageUrl(src, { width: px * 3 }) : "";
  const showImage = resolved !== "" && failedSrc !== resolved;
  const initial = (fallbackText ?? alt).trim().charAt(0).toUpperCase();

  // ハイドレーション前に読み込みに失敗した <img> は onError が発火しないため、ref で状態を確認する
  const imgRef = useCallback(
    (node: HTMLImageElement | null) => {
      if (!node?.complete || node.naturalWidth > 0) return;
      // naturalWidth 0 = 読み込み失敗（ただし固有サイズの無い SVG の可能性もあるため decode() で確認）
      node.decode().catch(() => {
        console.warn("[Avatar] failed to load:", node.currentSrc || resolved);
        setFailedSrc(resolved);
      });
    },
    [resolved],
  );

  const inner = (
    <span
      className="relative flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-ig-elevated text-ig-secondary"
      style={{ width: px, height: px }}
    >
      {showImage ? (
        <img
          ref={imgRef}
          src={resolved}
          alt={alt}
          width={px}
          height={px}
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => {
            console.warn("[Avatar] failed to load:", resolved);
            setFailedSrc(resolved);
          }}
          className="h-full w-full object-cover"
        />
      ) : initial ? (
        <span
          aria-label={alt}
          role="img"
          className="font-semibold text-ig-secondary"
          style={{ fontSize: Math.max(10, Math.round(px * 0.42)) }}
        >
          {initial}
        </span>
      ) : (
        <UserIcon size={Math.round(px * 0.6)} strokeWidth={1.5} title={alt} />
      )}
      {/* Instagram と同様、画像の縁に薄い枠 */}
      <span className="pointer-events-none absolute inset-0 rounded-full border border-black/10 dark:border-white/10" />
    </span>
  );

  if (ring === "none") {
    return <span className={cn("inline-flex shrink-0", className)}>{inner}</span>;
  }

  // リング幅と隙間はサイズに応じて調整（Instagram はおよそ 2px + 2〜3px）
  const ringWidth = ring === "active" ? 1.5 : px >= 56 ? 2.5 : 2;
  const gap = ring === "active" ? 1.5 : px >= 56 ? 3 : 2;

  return (
    <span
      className={cn(
        "inline-flex shrink-0 rounded-full",
        ring === "story" && "ig-story-ring",
        ring === "seen" && "bg-ig-separator",
        ring === "active" && "bg-ig-text",
        className,
      )}
      style={{ padding: ringWidth }}
    >
      <span className="inline-flex rounded-full bg-ig-bg" style={{ padding: gap }}>
        {inner}
      </span>
    </span>
  );
}
