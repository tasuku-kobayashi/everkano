"use client";

import { MEMORY_TAG_SECRET, type MemoryDTO, type UpdateMemoryRequest } from "@everkano/shared";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { TrashIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { formatRelativeTime } from "@/lib/format";
import {
  importanceToLevel,
  isSecretMemory,
  isSummaryMemory,
  isTempMemoryId,
  levelToImportance,
  MEMORY_CONTENT_MAX,
  withTag,
} from "@/lib/queries/memories";
import { PriorityControl } from "./priority-control";
import { SecretChip } from "./secret-chip";

export interface MemoryItemProps {
  memory: MemoryDTO;
  onUpdate: (memoryId: string, patch: UpdateMemoryRequest) => void;
  onDelete: (memory: MemoryDTO) => void;
}

/** 記憶 1 件（内容・バッジ・優先度・秘密・編集・削除） */
export function MemoryItem({ memory, onUpdate, onDelete }: MemoryItemProps) {
  const [editing, setEditing] = useState(false);
  const saving = isTempMemoryId(memory.id);
  const secret = isSecretMemory(memory);
  const summary = isSummaryMemory(memory);

  return (
    <li
      className={cn("border-ig-sheet-separator border-b px-4 pt-2 pb-3.5", saving && "opacity-60")}
      aria-busy={saving || undefined}
    >
      <div className="mb-1 flex min-h-8 items-center gap-1.5">
        {summary ? <Badge>🗒 会話の要約</Badge> : null}
        {memory.is_user_edited ? <Badge>編集済み</Badge> : null}
        <span className="text-ig-secondary text-[12px] leading-4">
          {saving ? "保存中…" : formatRelativeTime(memory.created_at)}
        </span>
        {editing ? null : (
          <div className="-mr-2 ml-auto flex items-center">
            <button
              type="button"
              disabled={saving}
              onClick={() => setEditing(true)}
              className="pressable text-ig-text h-8 px-2 text-[13px] font-semibold disabled:opacity-40"
            >
              編集
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => onDelete(memory)}
              aria-label="この記憶を削除"
              className="pressable text-ig-secondary flex size-8 items-center justify-center disabled:opacity-40"
            >
              <TrashIcon size={18} />
            </button>
          </div>
        )}
      </div>

      {editing ? (
        <EditForm
          initial={memory.content}
          onCancel={() => setEditing(false)}
          onSave={(content) => {
            setEditing(false);
            if (content !== memory.content) onUpdate(memory.id, { content });
          }}
        />
      ) : (
        <p className="text-wrap-anywhere text-[15px] leading-5 whitespace-pre-wrap">
          {memory.content}
        </p>
      )}

      {editing ? null : (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <PriorityControl
            value={importanceToLevel(memory.importance)}
            disabled={saving}
            onChange={(level) => onUpdate(memory.id, { importance: levelToImportance(level) })}
          />
          <SecretChip
            active={secret}
            disabled={saving}
            onToggle={(next) =>
              onUpdate(memory.id, { tags: withTag(memory.tags, MEMORY_TAG_SECRET, next) })
            }
          />
        </div>
      )}
    </li>
  );
}

function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="border-ig-sheet-separator text-ig-secondary inline-flex h-5 items-center rounded border px-1.5 text-[11px] leading-none font-semibold">
      {children}
    </span>
  );
}

function EditForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (content: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const trimmed = value.trim();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (trimmed) onSave(trimmed);
      }}
    >
      <label className="sr-only" htmlFor={id}>
        記憶の内容
      </label>
      <textarea
        id={id}
        ref={ref}
        value={value}
        maxLength={MEMORY_CONTENT_MAX}
        rows={3}
        onChange={(event) => setValue(event.target.value)}
        className="border-ig-input-border bg-ig-input-bg focus:border-ig-secondary w-full resize-none rounded-lg border px-3 py-2 text-[16px] leading-[22px] outline-none"
      />
      <div className="mt-2 flex items-center justify-end gap-2">
        <span className="text-ig-secondary mr-auto text-[12px]">
          {value.length}/{MEMORY_CONTENT_MAX}
        </span>
        <Button variant="secondary" size="sm" onClick={onCancel}>
          キャンセル
        </Button>
        <Button type="submit" size="sm" disabled={!trimmed}>
          保存
        </Button>
      </div>
    </form>
  );
}
