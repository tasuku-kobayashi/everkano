import { useMemo, useState } from "react";
import clsx from "clsx";
import { api, fetchBinary, getErrorMessage, unwrap, type ImageItem } from "../api/client";
import { useCharacters, useImages, useInvalidateAfterJob, usePatchImage, type ImageQuery } from "../api/queries";
import { ImageGrid } from "../components/ImageGrid";
import { ImageViewer } from "../components/ImageViewer";
import { useToast } from "../components/Toast";
import { useDebounce } from "../hooks/useDebounce";
import { useShortcuts } from "../hooks/useKeyboard";
import { copyText, dayEndIso, dayStartIso, downloadBlob, isoToLocalDateInput, METHOD_LABEL } from "../lib/format";
import { parseTags } from "../lib/prompt";
import { useUiStore } from "../store/ui";

/** Screen 4: cross-character browsing, filters, grouping, selection with bulk actions, compare tray, ZIP. */
export function GalleryPage() {
  const toast = useToast();
  const { data: characters } = useCharacters({ sort: "name" });
  const [filters, setFilters] = useState<ImageQuery>({ kind: "generated" });
  const [q, setQ] = useState("");
  const [seed, setSeed] = useState("");
  const [minSim, setMinSim] = useState(0);
  const [group, setGroup] = useState(false);
  const debouncedQ = useDebounce(q);
  const debouncedSeed = useDebounce(seed);
  const query: ImageQuery = useMemo(
    () => ({ ...filters, q: debouncedQ || undefined, seed: debouncedSeed ? Number(debouncedSeed) : undefined, min_similarity: minSim > 0 ? minSim : undefined, limit: 500 }),
    [filters, debouncedQ, debouncedSeed, minSim],
  );
  const { data, isLoading, error } = useImages(query);
  const items = useMemo(() => data?.items ?? [], [data]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [viewer, setViewer] = useState<number | null>(null);
  const patch = usePatchImage();
  const invalidate = useInvalidateAfterJob();
  const toggleTray = useUiStore((s) => s.toggleTray);
  const compareTray = useUiStore((s) => s.compareTray);

  // S / C act on the current selection while the viewer is closed (the viewer has its own S / C for the open image).
  useShortcuts(
    useMemo(
      () => ({
        s: () => {
          const chosen = items.filter((img) => selected.has(img.id));
          if (!chosen.length) return;
          const favorite = !chosen.every((img) => img.favorite);
          for (const img of chosen) if (img.favorite !== favorite) patch.mutate({ id: img.id, body: { favorite } });
        },
        c: () => {
          for (const id of selected) if (!compareTray.includes(id)) toggleTray(id);
        },
      }),
      [items, selected, compareTray, patch, toggleTray],
    ),
    viewer === null,
  );

  const toggleSelect = (img: ImageItem) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(img.id)) next.delete(img.id);
      else next.add(img.id);
      return next;
    });
  const ids = Array.from(selected);

  const bulk = async (body: { favorite?: boolean; add_tags?: string[]; remove_tags?: string[] }) => {
    try {
      const r = unwrap(await api.POST("/api/images/bulk", { body: { image_ids: ids, ...body } }));
      toast.success(`${r.updated} 枚を更新しました`);
      invalidate();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const bulkDelete = async () => {
    if (!window.confirm(`${ids.length} 枚を削除します（論理削除。ファイルは残ります）。よろしいですか？`)) return;
    try {
      const r = unwrap(await api.POST("/api/images/bulk-delete", { body: { image_ids: ids } }));
      toast.success(`${r.updated} 枚を削除しました`);
      setSelected(new Set());
      invalidate();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const zip = async () => {
    try {
      const blob = await fetchBinary("/api/images/zip", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image_ids: ids }) });
      downloadBlob(blob, `portrait-studio-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.zip`);
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const copyParams = async () => {
    const details = await Promise.all(ids.map(async (id) => unwrap(await api.GET("/api/images/{image_id}", { params: { path: { image_id: id } } }))));
    await copyText(JSON.stringify(details.map((d) => d.params_snapshot), null, 2));
    toast.success(`${details.length} 件のパラメータをコピーしました`);
  };
  const addTag = async () => {
    const text = window.prompt("追加するタグ（カンマ区切り）");
    if (text) await bulk({ add_tags: parseTags(text) });
  };

  const groups = useMemo(() => {
    if (!group) return null;
    const map = new Map<string, ImageItem[]>();
    for (const img of items) {
      const key = img.character_name ?? "（キャラなし）";
      map.set(key, [...(map.get(key) ?? []), img]);
    }
    return Array.from(map.entries());
  }, [group, items]);

  return (
    <div className="flex h-full flex-col" data-testid="gallery">
      <div className="flex flex-wrap items-end gap-2 border-b border-ink-700 p-3 text-xs">
        <label>
          <span className="label">キャラ</span>
          <select className="input w-40" value={filters.character_id ?? ""} onChange={(e) => setFilters({ ...filters, character_id: e.target.value || undefined })} data-testid="filter-character">
            <option value="">すべて</option>
            {characters?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">種類</span>
          <select className="input w-28" value={filters.kind ?? ""} onChange={(e) => setFilters({ ...filters, kind: (e.target.value || undefined) as ImageQuery["kind"] })}>
            <option value="">すべて</option>
            <option value="generated">生成</option>
            <option value="verify">検証</option>
            <option value="draft">種顔</option>
            <option value="upload">アップロード</option>
          </select>
        </label>
        <label>
          <span className="label">期間 から</span>
          <input type="date" className="input w-36" value={isoToLocalDateInput(filters.from)} onChange={(e) => setFilters({ ...filters, from: e.target.value ? dayStartIso(e.target.value) : undefined })} />
        </label>
        <label>
          <span className="label">まで</span>
          <input type="date" className="input w-36" value={isoToLocalDateInput(filters.to)} onChange={(e) => setFilters({ ...filters, to: e.target.value ? dayEndIso(e.target.value) : undefined })} />
        </label>
        <label>
          <span className="label">seed</span>
          <input className="input w-28 font-mono" value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} />
        </label>
        <label>
          <span className="label">手法</span>
          <select className="input w-40" value={filters.face_method ?? ""} onChange={(e) => setFilters({ ...filters, face_method: (e.target.value || undefined) as ImageQuery["face_method"] })}>
            <option value="">すべて</option>
            {Object.entries(METHOD_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">類似度の下限 ({minSim.toFixed(2)})</span>
          <input type="range" min={0} max={1} step={0.05} value={minSim} onChange={(e) => setMinSim(Number(e.target.value))} data-testid="min-similarity" />
        </label>
        <label className="flex items-center gap-1 self-center">
          <input type="checkbox" checked={!!filters.favorite} onChange={(e) => setFilters({ ...filters, favorite: e.target.checked ? true : undefined })} /> お気に入り
        </label>
        <label>
          <span className="label">タグ</span>
          <input className="input w-28" value={filters.tag ?? ""} onChange={(e) => setFilters({ ...filters, tag: e.target.value || undefined })} />
        </label>
        <label>
          <span className="label">テキスト検索</span>
          <input className="input w-40" value={q} onChange={(e) => setQ(e.target.value)} placeholder="プロンプト・キャラ名" />
        </label>
        <label className="flex items-center gap-1 self-center">
          <input type="checkbox" checked={group} onChange={(e) => setGroup(e.target.checked)} /> キャラ別にグループ表示
        </label>
        <span className="self-center text-slate-400" data-testid="gallery-total">
          {data ? `${data.total} 枚` : ""}
        </span>
      </div>

      {ids.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 bg-ink-900 px-3 py-2 text-xs" data-testid="bulk-bar">
          <span>{ids.length} 枚を選択中</span>
          <button className="btn-secondary" onClick={() => bulk({ favorite: true })}>
            ★ お気に入り
          </button>
          <button className="btn-secondary" onClick={() => bulk({ favorite: false })}>
            ☆ 解除
          </button>
          <button className="btn-secondary" onClick={addTag}>
            タグ付け
          </button>
          <button className="btn-secondary" onClick={copyParams}>
            パラメータをコピー
          </button>
          <button className="btn-secondary" onClick={zip} data-testid="zip-download">
            ZIP でダウンロード（params_snapshot 同梱）
          </button>
          <button className="btn-secondary" onClick={() => ids.forEach((id) => toggleTray(id))}>
            比較トレイへ
          </button>
          <button className="btn-danger" onClick={bulkDelete}>
            削除
          </button>
          <button className="btn-ghost" onClick={() => setSelected(new Set())}>
            選択解除
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 p-2">
        {error && <p className="text-sm text-rose-300">{getErrorMessage(error)}</p>}
        {isLoading && <div className="skeleton h-64" />}
        {!isLoading && !groups && (
          <ImageGrid images={items} onOpen={setViewer} onFavorite={(img) => patch.mutate({ id: img.id, body: { favorite: !img.favorite } })} selectedIds={selected} onSelect={toggleSelect} showCharacter />
        )}
        {groups && (
          <div className="h-full space-y-4 overflow-auto">
            {groups.map(([name, imgs]) => (
              <section key={name}>
                <h2 className={clsx("mb-1 text-sm font-medium")}>
                  {name} <span className="text-xs text-slate-500">{imgs.length} 枚</span>
                </h2>
                <ImageGrid images={imgs} onOpen={(i) => setViewer(items.indexOf(imgs[i]!))} height={Math.min(700, Math.ceil(imgs.length / 5) * 360)} selectedIds={selected} onSelect={toggleSelect} onFavorite={(img) => patch.mutate({ id: img.id, body: { favorite: !img.favorite } })} />
              </section>
            ))}
          </div>
        )}
      </div>
      <ImageViewer images={items} index={viewer} onClose={() => setViewer(null)} onIndexChange={setViewer} />
    </div>
  );
}
