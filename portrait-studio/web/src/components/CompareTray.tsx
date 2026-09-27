import { useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { keys, useCharacter } from "../api/queries";
import { useUiStore } from "../store/ui";
import { SimilarityBadge } from "./Badges";
import { BlurImage } from "./BlurImage";

/** Bottom drawer: the reference face is always pinned at the left, followed by the images in the tray. */
export function CompareTray() {
  const tray = useUiStore((s) => s.compareTray);
  const toggleTray = useUiStore((s) => s.toggleTray);
  const clear = useUiStore((s) => s.clearTray);
  const [open, setOpen] = useState(true);
  const queries = useQueries({
    queries: tray.map((id) => ({
      queryKey: keys.image(id),
      queryFn: async () => unwrap(await api.GET("/api/images/{image_id}", { params: { path: { image_id: id } } })),
    })),
  });
  const images = queries.map((q) => q.data).filter((x): x is NonNullable<typeof x> => !!x);
  const characterId = images.find((i) => i.character_id)?.character_id ?? undefined;
  const { data: character } = useCharacter(characterId);
  if (!tray.length) return null;
  return (
    <div className="border-t border-ink-700 bg-ink-900" data-testid="compare-tray">
      <div className="flex items-center justify-between px-4 py-1 text-xs text-slate-300">
        <button className="btn-ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "▾" : "▸"} 比較トレイ（{tray.length} 枚）
        </button>
        <button className="btn-ghost" onClick={clear}>
          すべて外す
        </button>
      </div>
      {open && (
        <div className="flex gap-2 overflow-x-auto px-4 pb-3">
          <div className="shrink-0 w-32">
            <div className="mb-1 text-[11px] text-emerald-300">参照顔（固定）</div>
            {character?.thumbnail_url ? (
              <img src={character.thumbnail_url} alt={`${character.name} の参照顔`} className="aspect-[3/4] w-32 rounded object-cover ring-2 ring-emerald-500" />
            ) : (
              <div className="skeleton aspect-[3/4] w-32" />
            )}
            <div className="truncate text-[11px] text-slate-400">{character?.name ?? "—"}</div>
          </div>
          {images.map((img) => (
            <div key={img.id} className="relative shrink-0 w-32">
              <div className="mb-1 flex items-center justify-between text-[11px]">
                <span className="font-mono text-slate-400">seed {img.seed}</span>
                <button className="text-rose-300 hover:underline" onClick={() => toggleTray(img.id)} aria-label="トレイから外す">
                  ✕
                </button>
              </div>
              <BlurImage id={img.id} src={img.thumb_url} alt={img.prompt ?? "画像"} className="aspect-[3/4] w-32 rounded" />
              <div className="mt-1">
                <SimilarityBadge value={img.similarity} status={img.similarity_status} compact />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
