"use client";

import { MEMORY_TAG_SECRET, type MemoryDTO, type UpdateMemoryRequest } from "@everkano/shared";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { BookmarkIcon, PlusIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { getErrorMessage } from "@/lib/api/errors";
import {
  DEFAULT_MEMORY_LEVEL,
  levelToImportance,
  MEMORY_CONTENT_MAX,
  nextTempMemoryId,
  stableMemoryOrder,
  useCreateMemory,
  useDeleteMemory,
  useMemories,
  useUpdateMemory,
  type MemoryLevel,
} from "@/lib/queries/memories";
import { MemoryItem } from "./memory-item";
import { PriorityControl } from "./priority-control";
import { SecretChip } from "./secret-chip";

export interface MemoryPanelProps {
  open: boolean;
  onClose: () => void;
  characterId: string;
  characterName: string;
}

/**
 * メモリパネル（DM ヘッダーの「i」、仕様 §5.6 / §9.4 / C-6）。
 * そのキャラが覚えていることの一覧・追加・編集・削除・優先度変更・「二人だけの秘密」。
 * 一覧も更新もすべて Python API（/memories）経由。
 */
export function MemoryPanel({ open, onClose, characterId, characterName }: MemoryPanelProps) {
  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title={`${characterName}が覚えていること`}
      className="h-[85dvh]"
    >
      <MemoryPanelContent characterId={characterId} characterName={characterName} />
    </BottomSheet>
  );
}

function MemoryPanelContent({
  characterId,
  characterName,
}: {
  characterId: string;
  characterName: string;
}) {
  const toast = useToast();
  const memoriesQuery = useMemories(characterId);
  const createMemory = useCreateMemory(characterId);
  const updateMemory = useUpdateMemory(characterId);
  const deleteMemory = useDeleteMemory(characterId);
  const [deleting, setDeleting] = useState<MemoryDTO | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // パネルを開いている間は表示順を固定（優先度を変えても行が飛ばない）。閉じると unmount されてリセット
  const memories = memoriesQuery.data;
  const [order, setOrder] = useState<string[]>([]);
  const nextOrder = useMemo(
    () => (memories ? stableMemoryOrder(order, memories) : order),
    [memories, order],
  );
  if (nextOrder.length !== order.length || nextOrder.some((id, index) => id !== order[index])) {
    setOrder(nextOrder);
  }
  const ordered = useMemo(() => {
    const byId = new Map((memories ?? []).map((memory) => [memory.id, memory]));
    return nextOrder.flatMap((id) => {
      const memory = byId.get(id);
      return memory ? [memory] : [];
    });
  }, [memories, nextOrder]);

  const onError = (error: unknown) => toast.error(getErrorMessage(error));

  const onUpdate = (memoryId: string, patch: UpdateMemoryRequest) =>
    updateMemory.mutate({ memoryId, patch }, { onError });

  const confirmDelete = () => {
    const target = deleting;
    if (!target) return;
    setDeleting(null);
    // 確認ダイアログは閉じるとき削除ボタンにフォーカスを戻すが、その行は楽観的更新で消えているため
    // フォーカスがシートの外（body）に落ちて Esc で閉じられなくなる。閉じ終わった後にパネルへ戻す
    setTimeout(() => {
      const root = rootRef.current;
      if (root && !root.contains(document.activeElement)) root.focus({ preventScroll: true });
    }, 250);
    deleteMemory.mutate(
      { memoryId: target.id },
      {
        onSuccess: () => toast.show("記憶を削除しました"),
        onError,
      },
    );
  };

  return (
    <div ref={rootRef} tabIndex={-1} className="pb-4 outline-none">
      <p className="px-4 pt-3 pb-1 text-[13px] leading-[18px] text-ig-secondary">
        {characterName}
        は、あなたとの会話で大切だと感じたことを覚えています。覚えていてほしいことを追加したり、忘れてほしいことを削除したりできます。
      </p>

      <AddMemoryForm
        characterName={characterName}
        onSubmit={(content, level, secret) =>
          createMemory.mutate(
            {
              tempId: nextTempMemoryId(),
              request: {
                character_id: characterId,
                content,
                importance: levelToImportance(level),
                tags: secret ? [MEMORY_TAG_SECRET] : [],
              },
            },
            {
              onSuccess: () => toast.show(`${characterName}が覚えました`),
              onError,
            },
          )
        }
      />

      {memoriesQuery.isPending ? (
        <MemoryListSkeleton />
      ) : memoriesQuery.isError && !memories ? (
        <ErrorState
          compact
          message={getErrorMessage(memoriesQuery.error)}
          onRetry={() => void memoriesQuery.refetch()}
          retrying={memoriesQuery.isRefetching}
        />
      ) : ordered.length === 0 ? (
        <div className="flex flex-col items-center px-8 pt-10 pb-6 text-center">
          <div className="mb-3 flex size-14 items-center justify-center rounded-full border-2 border-ig-text">
            <BookmarkIcon size={26} strokeWidth={1.6} />
          </div>
          <p className="text-[15px] leading-5 font-semibold">
            まだ覚えていることはありません。たくさん話してみよう
          </p>
        </div>
      ) : (
        <ul
          aria-label={`${characterName}が覚えていること`}
          className="border-t border-ig-sheet-separator"
        >
          {ordered.map((memory) => (
            <MemoryItem
              key={memory.id}
              memory={memory}
              onUpdate={onUpdate}
              onDelete={setDeleting}
            />
          ))}
        </ul>
      )}

      <Modal
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title="この記憶を削除しますか？"
        description={`削除すると${characterName}はこのことを忘れます`}
        actions={[
          { label: "削除", variant: "destructive", onClick: confirmDelete },
          { label: "キャンセル", onClick: () => setDeleting(null) },
        ]}
      />
    </div>
  );
}

function AddMemoryForm({
  characterName,
  onSubmit,
}: {
  characterName: string;
  onSubmit: (content: string, level: MemoryLevel, secret: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [content, setContent] = useState("");
  const [level, setLevel] = useState<MemoryLevel>(DEFAULT_MEMORY_LEVEL);
  const [secret, setSecret] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const wasExpandedRef = useRef(false);
  const id = useId();
  const trimmed = content.trim();

  useEffect(() => {
    if (expanded) {
      textareaRef.current?.focus({ preventScroll: true });
    } else if (wasExpandedRef.current) {
      // 追加・キャンセルでフォームを閉じると、フォーカスしていたボタンが消えてフォーカスがシートの外（body）に
      // 落ち、Esc でシートを閉じられなくなる。「覚えてほしいことを追加」ボタンへ戻す
      addButtonRef.current?.focus({ preventScroll: true });
    }
    wasExpandedRef.current = expanded;
  }, [expanded]);

  const reset = () => {
    setExpanded(false);
    setContent("");
    setLevel(DEFAULT_MEMORY_LEVEL);
    setSecret(false);
  };

  if (!expanded) {
    return (
      <button
        ref={addButtonRef}
        type="button"
        onClick={() => setExpanded(true)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left pressable"
      >
        <span className="flex size-11 shrink-0 items-center justify-center rounded-full border border-ig-text">
          <PlusIcon size={22} strokeWidth={1.8} />
        </span>
        <span className="text-[15px] font-semibold">覚えてほしいことを追加</span>
      </button>
    );
  }

  return (
    <form
      className="px-4 pt-2 pb-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!trimmed) return;
        onSubmit(trimmed, level, secret);
        reset();
      }}
    >
      <label htmlFor={id} className="mb-1.5 block text-[13px] font-semibold">
        {characterName}に覚えていてほしいこと
      </label>
      <textarea
        id={id}
        ref={textareaRef}
        value={content}
        rows={3}
        maxLength={MEMORY_CONTENT_MAX}
        onChange={(event) => setContent(event.target.value)}
        placeholder="例: 10月2日（金）に大事なプレゼンがある"
        className="w-full resize-none rounded-lg border border-ig-input-border bg-ig-input-bg px-3 py-2 text-[16px] leading-[22px] outline-none placeholder:text-ig-secondary focus:border-ig-secondary"
      />
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
        <PriorityControl value={level} onChange={setLevel} />
        <SecretChip active={secret} onToggle={setSecret} />
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <span className="mr-auto text-[12px] text-ig-secondary">
          {content.length}/{MEMORY_CONTENT_MAX}
        </span>
        <Button variant="secondary" size="sm" onClick={reset}>
          キャンセル
        </Button>
        <Button type="submit" size="sm" disabled={!trimmed}>
          追加
        </Button>
      </div>
    </form>
  );
}

function MemoryListSkeleton() {
  return (
    <div className="border-t border-ig-sheet-separator" aria-busy="true" aria-label="読み込み中">
      {[0, 1, 2].map((i) => (
        <div key={i} className="space-y-2 border-b border-ig-sheet-separator px-4 py-4">
          <Skeleton shape="text" className="h-2.5 w-16" />
          <Skeleton shape="text" className="w-11/12" />
          <Skeleton shape="text" className="w-2/3" />
          <Skeleton className="mt-1 h-6 w-40 rounded-lg" />
        </div>
      ))}
    </div>
  );
}
