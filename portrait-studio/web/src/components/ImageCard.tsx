import clsx from "clsx";
import type { ImageItem } from "../api/client";
import { formatRelative } from "../lib/format";
import { useUiStore } from "../store/ui";
import { SimilarityBadge } from "./Badges";
import { BlurImage } from "./BlurImage";

interface Props {
  image: ImageItem;
  onOpen: () => void;
  onFavorite?: () => void;
  onRegenerate?: () => void;
  onSimilarity?: () => void;
  selected?: boolean;
  onSelect?: () => void;
  showCharacter?: boolean;
  compact?: boolean;
}

/** Thumbnail card (thumbnails only in lists). Hover actions: open / similarity / favorite / regenerate / compare. */
export function ImageCard({ image, onOpen, onFavorite, onRegenerate, onSimilarity, selected, onSelect, showCharacter, compact }: Props) {
  const inTray = useUiStore((s) => s.compareTray.includes(image.id));
  const toggleTray = useUiStore((s) => s.toggleTray);
  return (
    <div className={clsx("group card relative flex flex-col overflow-hidden", selected && "ring-2 ring-accent")} data-testid="image-card" data-image-id={image.id}>
      {onSelect && (
        <input
          type="checkbox"
          className="absolute left-1.5 top-1.5 z-10 h-4 w-4"
          checked={!!selected}
          onChange={onSelect}
          aria-label="選択"
          onClick={(e) => e.stopPropagation()}
        />
      )}
      <button className="block w-full text-left" onClick={onOpen} aria-label="画像を開く">
        <BlurImage id={image.id} src={image.thumb_url} alt={image.prompt ? `生成画像: ${image.prompt.slice(0, 60)}` : "生成画像"} className="aspect-[832/1216] w-full" />
      </button>
      <div className="flex items-center justify-between gap-1 px-1.5 py-1 text-[11px] text-slate-400">
        <span className="font-mono">seed {image.seed ?? "—"}</span>
        <SimilarityBadge value={image.similarity} status={image.similarity_status} compact />
      </div>
      {!compact && (
        <div className="flex items-center justify-between px-1.5 pb-1 text-[10px] text-slate-500">
          <span>
            {showCharacter && image.character_name ? `${image.character_name} · ` : ""}
            {image.character_version ? `v${image.character_version} · ` : ""}
            {formatRelative(image.created_at)}
          </span>
          {image.favorite && <span title="お気に入り">★</span>}
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 hidden items-center justify-center gap-1 bg-black/70 py-1 text-[11px] group-hover:flex group-focus-within:flex">
        <button className="rounded px-1.5 py-0.5 hover:bg-white/20" onClick={onOpen}>
          開く
        </button>
        {onSimilarity && (
          <button className="rounded px-1.5 py-0.5 hover:bg-white/20" onClick={onSimilarity} title="類似度を再計算">
            類似度
          </button>
        )}
        {onFavorite && (
          <button className="rounded px-1.5 py-0.5 hover:bg-white/20" onClick={onFavorite} aria-pressed={image.favorite} title="お気に入り (S)">
            {image.favorite ? "★" : "☆"}
          </button>
        )}
        {onRegenerate && (
          <button className="rounded px-1.5 py-0.5 hover:bg-white/20" onClick={onRegenerate} title="同一パラメータで再生成">
            再生成
          </button>
        )}
        <button className={clsx("rounded px-1.5 py-0.5 hover:bg-white/20", inTray && "text-accent")} onClick={() => toggleTray(image.id)} aria-pressed={inTray} title="比較トレイ (C)">
          比較
        </button>
      </div>
    </div>
  );
}
