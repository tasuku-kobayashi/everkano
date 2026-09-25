"use client";

import { CdnImage } from "@/components/ui/image";
import { LockFilledIcon, LockIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { formatPriceTokens } from "./paid-lock-modal";

/**
 * 有料投稿のプレビュー画像（全面ぼかし + 鍵）。
 *
 * posts.image_url は有料投稿ではもともとプレビュー画像（本体は post_private_assets でクライアントから到達不能）だが、
 * 画面上でも強いぼかしを掛けて内容が分からないようにする。
 * ぼかしの縁が背景色で白っぽく抜けないよう、画像を拡大して縁を枠外へ追い出す。
 */
export function PaidImage({
  src,
  alt,
  priceTokens,
  variant,
  priority,
  className,
}: {
  src: string;
  alt: string;
  priceTokens: number;
  /** full: フィード・投稿詳細（大きな鍵 + 「有料コンテンツ」バッジ） / tile: グリッドのサムネイル（小さな鍵 + 価格） */
  variant: "full" | "tile";
  priority?: boolean;
  className?: string;
}) {
  const tile = variant === "tile";
  return (
    <span className={cn("relative block overflow-hidden", className)}>
      <CdnImage
        src={src}
        alt={alt}
        className="aspect-square w-full"
        widths={tile ? [320] : [640, 1080]}
        sizes={tile ? "(max-width: 480px) 33vw, 160px" : undefined}
        blurred
        priority={priority}
        imgClassName={tile ? "scale-[1.8]! blur-lg!" : "scale-150! blur-xl!"}
      />
      <span aria-hidden="true" className="absolute inset-0 bg-black/25" />
      {tile ? (
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-white">
          <LockFilledIcon size={22} className="drop-shadow-[0_1px_2px_rgb(0_0_0/0.45)]" />
          <span className="text-[11px] leading-3 font-semibold [text-shadow:0_1px_2px_rgb(0_0_0/0.5)]">
            {formatPriceTokens(priceTokens)}
          </span>
        </span>
      ) : (
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-white">
          <span className="flex size-[72px] items-center justify-center rounded-full border-2 border-white/90 bg-black/15">
            <LockIcon size={34} strokeWidth={1.7} />
          </span>
          <span className="flex items-center gap-1 rounded-full bg-black/55 px-3 py-1.5 text-[13px] leading-4 font-semibold">
            <LockFilledIcon size={13} />
            有料コンテンツ
          </span>
          <span className="text-[13px] leading-4 font-medium text-white/90 [text-shadow:0_1px_2px_rgb(0_0_0/0.4)]">
            タップして詳細を見る
          </span>
        </span>
      )}
    </span>
  );
}
