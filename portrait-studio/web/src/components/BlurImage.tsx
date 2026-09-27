import { useState } from "react";
import clsx from "clsx";
import { useUiStore } from "../store/ui";

interface Props {
  id: string;
  src: string;
  alt: string;
  className?: string;
  imgClassName?: string;
  loading?: "lazy" | "eager";
}

/**
 * NSFW blur: blurred by default (store.blurDefault). Hover reveals temporarily, click / Enter reveals persistently
 * for this image in this session. Screen-share safe by default.
 */
export function BlurImage({ id, src, alt, className, imgClassName, loading = "lazy" }: Props) {
  const blurDefault = useUiStore((s) => s.blurDefault);
  const revealed = useUiStore((s) => !!s.revealed[id]);
  const reveal = useUiStore((s) => s.reveal);
  const [hover, setHover] = useState(false);
  const blurred = blurDefault && !revealed && !hover;
  return (
    <div
      className={clsx("relative overflow-hidden bg-ink-800", className)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={() => blurred && reveal(id)}
      onKeyDown={(e) => {
        if (blurred && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          reveal(id);
        }
      }}
      tabIndex={blurred ? 0 : -1}
      role={blurred ? "button" : undefined}
      aria-label={blurred ? `${alt}（ぼかし中。クリックで表示）` : undefined}
      data-blurred={blurred ? "true" : "false"}
    >
      <img src={src} alt={alt} loading={loading} draggable={false} className={clsx("h-full w-full object-cover transition-[filter] duration-150", blurred && "blur-nsfw", imgClassName)} />
      {blurred && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="rounded bg-black/60 px-2 py-1 text-[11px] text-white">ぼかし中 — ホバー / クリックで表示</span>
        </div>
      )}
    </div>
  );
}
