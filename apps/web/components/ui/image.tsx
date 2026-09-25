"use client";

import { useCallback, useState, type ImgHTMLAttributes } from "react";
import { cn } from "@/lib/cn";
import { buildSrcSet, getStorageAdapter, IMAGE_WIDTHS } from "@/lib/storage";
import { ImageIcon } from "./icons";

export interface CdnImageProps extends Omit<
  ImgHTMLAttributes<HTMLImageElement>,
  "src" | "srcSet" | "width" | "height"
> {
  /** DB の値（絶対URL またはオブジェクトキー）。StorageAdapter で解決する */
  src: string | null | undefined;
  alt: string;
  /** 外側の箱のクラス。サイズ（例: "aspect-square w-full"）はここで指定する */
  className?: string;
  /** <img> 自体のクラス（既定で object-cover・全面） */
  imgClassName?: string;
  /** srcSet に使う幅。既定 320 / 640 / 1080 */
  widths?: readonly number[];
  /** 既定: スマホ幅いっぱい（最大 480px） */
  sizes?: string;
  /** 画質（CDN が対応する場合） */
  quality?: number;
  /** 有料投稿: CDN ぼかし + 強い CSS ぼかし */
  blurred?: boolean;
  /** 読み込み後にフェードイン（既定 true） */
  fadeIn?: boolean;
  /** ファーストビューの画像（lazy を無効化し fetchpriority=high） */
  priority?: boolean;
}

/**
 * CDN 画像ラッパー（next/image は使わない方針）。
 * - StorageAdapter で URL を解決し、変換対応ドライバーなら srcSet（320/640/1080w）を付ける
 * - loading="lazy" / decoding="async"、読み込み完了でフェードイン
 * - 読み込み失敗時はグレーのプレースホルダー（画像アイコン）を表示
 * - 箱いっぱいに object-cover で表示する。箱のサイズは className で指定
 */
export function CdnImage({
  src,
  alt,
  className,
  imgClassName,
  widths = IMAGE_WIDTHS,
  sizes = "(max-width: 480px) 100vw, 480px",
  quality,
  blurred = false,
  fadeIn = true,
  priority = false,
  onLoad,
  onError,
  ...rest
}: CdnImageProps) {
  const [loaded, setLoaded] = useState(false);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  const storage = getStorageAdapter();
  const blur = blurred ? 60 : undefined;
  const largest = widths[widths.length - 1] ?? 1080;
  const resolved = src ? storage.resolveImageUrl(src, { width: largest, quality, blur }) : "";
  const srcSet = src ? buildSrcSet(src, widths, { quality, blur }, storage) : undefined;
  const failed = !resolved || failedSrc === resolved;

  // SSR 済みの <img> がハイドレーション前に読み込み完了（または失敗）していると
  // onLoad / onError が発火しないため、ref で状態を確認する
  const imgRef = useCallback(
    (node: HTMLImageElement | null) => {
      if (!node?.complete) return;
      if (node.naturalWidth > 0) {
        setLoaded(true);
        return;
      }
      // naturalWidth 0 = 読み込み失敗（ただし固有サイズの無い SVG の可能性もあるため decode() で確認）
      node.decode().then(
        () => setLoaded(true),
        () => {
          console.warn("[CdnImage] failed to load:", node.currentSrc || resolved);
          setFailedSrc(resolved);
        },
      );
    },
    [resolved],
  );

  return (
    <span className={cn("relative block overflow-hidden bg-ig-elevated", className)}>
      {failed ? (
        <span className="absolute inset-0 flex items-center justify-center text-ig-secondary">
          <ImageIcon size={32} strokeWidth={1.5} />
          <span className="sr-only">{alt}</span>
        </span>
      ) : (
        <img
          ref={imgRef}
          src={resolved}
          srcSet={srcSet}
          sizes={srcSet ? sizes : undefined}
          alt={alt}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={priority ? "high" : undefined}
          draggable={false}
          onLoad={(event) => {
            setLoaded(true);
            onLoad?.(event);
          }}
          onError={(event) => {
            console.warn("[CdnImage] failed to load:", resolved);
            setFailedSrc(resolved);
            onError?.(event);
          }}
          className={cn(
            "absolute inset-0 h-full w-full object-cover",
            fadeIn && "transition-opacity duration-300",
            fadeIn && !loaded && "opacity-0",
            blurred && "scale-110 blur-2xl",
            imgClassName,
          )}
          {...rest}
        />
      )}
    </span>
  );
}
