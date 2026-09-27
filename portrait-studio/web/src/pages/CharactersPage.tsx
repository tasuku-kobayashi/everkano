import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, getErrorMessage, unwrap, type CharacterSummary } from "../api/client";
import { useCharacters } from "../api/queries";
import { useQueryClient } from "@tanstack/react-query";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { useDebounce } from "../hooks/useDebounce";
import { formatRelative, METHOD_LABEL } from "../lib/format";
import { parseTags } from "../lib/prompt";
import { VersionsDialog } from "./VersionsDialog";

type Sort = "recent" | "name" | "generations";

/** Screen 1: characters are first-class objects (cards), not a dropdown. */
export function CharactersPage() {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("recent");
  const debounced = useDebounce(q);
  const { data, isLoading, error } = useCharacters({ q: debounced || undefined, sort });
  const [menuFor, setMenuFor] = useState<CharacterSummary | null>(null);
  const [renaming, setRenaming] = useState<CharacterSummary | null>(null);
  const [deleting, setDeleting] = useState<CharacterSummary | null>(null);
  const [versionsFor, setVersionsFor] = useState<CharacterSummary | null>(null);

  return (
    <div className="p-4">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Link to="/create" className="btn-primary text-base" data-testid="create-character">
          ＋ 新規キャラクター作成
        </Link>
        <input className="input max-w-xs" placeholder="名前・タグで検索" value={q} onChange={(e) => setQ(e.target.value)} aria-label="検索" />
        <label className="flex items-center gap-2 text-sm text-slate-300">
          並び替え
          <select className="input w-auto" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="並び替え">
            <option value="recent">最近</option>
            <option value="name">名前</option>
            <option value="generations">生成数</option>
          </select>
        </label>
        {data && <span className="text-xs text-slate-500">{data.length} 体</span>}
      </div>

      {error && <p className="text-sm text-rose-300">{getErrorMessage(error)}</p>}
      {isLoading && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton aspect-[3/4]" />
          ))}
        </div>
      )}
      {data && data.length === 0 && (
        <div className="card mx-auto mt-10 max-w-lg p-8 text-center" data-testid="empty-state">
          <h2 className="text-xl font-semibold">まず 1 体作りましょう</h2>
          <p className="mt-2 text-sm text-slate-400">
            種となる顔をテキストから生成するか画像をアップロードし、品質を測って参照顔を選び、同一性を検証してから登録します。
            登録後はワークスペースで何百回でも同じ人物を生成できます。
          </p>
          <Link to="/create" className="btn-primary mt-6 text-base">
            ＋ 新規キャラクター作成
          </Link>
        </div>
      )}
      {data && data.length > 0 && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3" data-testid="character-grid">
          {data.map((c) => (
            <CharacterCard key={c.id} character={c} onMenu={() => setMenuFor(c)} />
          ))}
        </div>
      )}

      {menuFor && (
        <CardMenu
          character={menuFor}
          onClose={() => setMenuFor(null)}
          onRename={() => {
            setRenaming(menuFor);
            setMenuFor(null);
          }}
          onDelete={() => {
            setDeleting(menuFor);
            setMenuFor(null);
          }}
          onVersions={() => {
            setVersionsFor(menuFor);
            setMenuFor(null);
          }}
        />
      )}
      {renaming && <RenameDialog character={renaming} onClose={() => setRenaming(null)} />}
      {deleting && <DeleteDialog character={deleting} onClose={() => setDeleting(null)} />}
      {versionsFor && <VersionsDialog characterId={versionsFor.id} onClose={() => setVersionsFor(null)} />}
    </div>
  );
}

function CharacterCard({ character: c, onMenu }: { character: CharacterSummary; onMenu: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="card group relative flex flex-col overflow-hidden" data-testid="character-card">
      <button className="relative block aspect-[3/4] w-full bg-ink-800 text-left" onClick={() => navigate(`/workspace/${c.id}`)} aria-label={`${c.name} で生成`}>
        {c.thumbnail_url ? <img src={c.thumbnail_url} alt={`${c.name} の代表画像`} className="h-full w-full object-cover" loading="lazy" /> : <div className="skeleton h-full w-full" />}
        <div className="absolute bottom-1 left-1 flex gap-1">
          {c.reference_urls.slice(0, 4).map((url, i) => (
            <img key={url} src={url} alt={`参照顔 ${i + 1}`} className="h-9 w-9 rounded border border-white/60 object-cover shadow" loading="lazy" />
          ))}
        </div>
        <span className="absolute right-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">v{c.current_version}</span>
      </button>
      <div className="flex items-start justify-between gap-1 px-2 pt-2">
        <div className="min-w-0">
          <div className="truncate font-medium" title={c.name}>
            {c.name}
          </div>
          <div className="truncate text-[11px] text-slate-400">{c.face_method ? METHOD_LABEL[c.face_method] : ""}</div>
        </div>
        <button className="btn-ghost px-2" onClick={onMenu} aria-label={`${c.name} のメニュー`} data-testid="card-menu">
          ⋯
        </button>
      </div>
      <div className="flex flex-wrap gap-1 px-2 pt-1">
        {c.tags.map((t) => (
          <span key={t} className="badge bg-ink-700 text-slate-300">
            {t}
          </span>
        ))}
      </div>
      <div className="mt-auto flex justify-between px-2 py-2 text-[11px] text-slate-400">
        <span>生成 {c.stats.generations} 枚</span>
        <span>{formatRelative(c.stats.last_used)}</span>
      </div>
    </div>
  );
}

function CardMenu({ character, onClose, onRename, onDelete, onVersions }: { character: CharacterSummary; onClose: () => void; onRename: () => void; onDelete: () => void; onVersions: () => void }) {
  const navigate = useNavigate();
  return (
    <Modal open onClose={onClose} title={character.name} testId="card-menu-dialog">
      <div className="flex flex-col gap-1">
        <button className="btn-primary justify-start" onClick={() => navigate(`/workspace/${character.id}`)}>
          🎨 このキャラで生成
        </button>
        <button className="btn-secondary justify-start" onClick={onVersions}>
          🗂 版を管理（参照顔の差し替え・ロールバック）
        </button>
        <button className="btn-secondary justify-start" onClick={onRename}>
          ✏ 名前・タグ・説明を編集
        </button>
        <button className="btn-danger justify-start" onClick={onDelete}>
          🗑 削除
        </button>
      </div>
    </Modal>
  );
}

function RenameDialog({ character, onClose }: { character: CharacterSummary; onClose: () => void }) {
  const [name, setName] = useState(character.name);
  const [tags, setTags] = useState(character.tags.join(", "));
  const [description, setDescription] = useState(character.description);
  const qc = useQueryClient();
  const toast = useToast();
  const save = async () => {
    try {
      unwrap(await api.PATCH("/api/characters/{character_id}", { params: { path: { character_id: character.id } }, body: { name, tags: parseTags(tags), description } }));
      await qc.invalidateQueries({ queryKey: ["characters"] });
      await qc.invalidateQueries({ queryKey: ["character", character.id] });
      toast.success("保存しました");
      onClose();
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  return (
    <Modal open onClose={onClose} title="名前・タグ・説明を編集">
      <p className="mb-3 text-xs text-slate-400">参照顔と固定設定はここでは変更できません（版の管理から新版として差し替えます）。</p>
      <label className="label">名前</label>
      <input className="input mb-2" value={name} onChange={(e) => setName(e.target.value)} />
      <label className="label">タグ（カンマ区切り）</label>
      <input className="input mb-2" value={tags} onChange={(e) => setTags(e.target.value)} />
      <label className="label">説明</label>
      <textarea className="input mb-3" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      <div className="flex justify-end gap-2">
        <button className="btn-secondary" onClick={onClose}>
          キャンセル
        </button>
        <button className="btn-primary" onClick={save} disabled={!name.trim()}>
          保存
        </button>
      </div>
    </Modal>
  );
}

function DeleteDialog({ character, onClose }: { character: CharacterSummary; onClose: () => void }) {
  const [deleteImages, setDeleteImages] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  return (
    <ConfirmDialog
      open
      title={`「${character.name}」を削除`}
      confirmLabel="削除する"
      onCancel={onClose}
      onConfirm={async () => {
        try {
          const r = unwrap(await api.DELETE("/api/characters/{character_id}", { params: { path: { character_id: character.id }, query: { delete_images: deleteImages } } }));
          toast.success(`削除しました（参照顔 ${r.deleted_references} 枚、生成画像 ${r.deleted_images} 枚）`);
          await qc.invalidateQueries({ queryKey: ["characters"] });
          await qc.invalidateQueries({ queryKey: ["images"] });
          onClose();
        } catch (e) {
          toast.error(getErrorMessage(e));
        }
      }}
    >
      <p className="font-medium text-rose-200">この操作で壊れるもの:</p>
      <ul className="list-disc space-y-1 pl-5 text-slate-300">
        <li>参照顔（全版）と埋め込みは即時に削除され、復元できません。</li>
        <li>このキャラで新しい画像を生成・再生成できなくなります。</li>
        <li>生成済み画像（{character.stats.generations} 枚）は既定では残ります（キャラ名のスナップショットのみ保持）。</li>
      </ul>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={deleteImages} onChange={(e) => setDeleteImages(e.target.checked)} />
        生成済み画像とサイドカー JSON もディスクから削除する
      </label>
    </ConfirmDialog>
  );
}
