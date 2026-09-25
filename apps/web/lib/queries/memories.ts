/**
 * メモリパネル（C-6）のデータ層。
 *
 * - 一覧・追加・更新・削除はすべて Python API（/memories）経由（書き込みは Gate #1 + 監査ログのため API 必須。
 *   一覧も API の並び順「重要度 → 新しい順」に揃える）。
 * - ミューテーションは楽観的更新 → 失敗時ロールバック → 最後の 1 件が終わったら再取得。
 *   トースト表示は呼び出し側（mutate の onError）で行う。
 */

import {
  MEMORY_TAG_SECRET,
  MEMORY_TAG_SUMMARY,
  type CreateMemoryRequest,
  type MemoryDTO,
  type UpdateMemoryRequest,
} from "@everkano/shared";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/queries/keys";

// ---------------------------------------------------------------------------
// 優先度（importance ↔ 3 段階）
// ---------------------------------------------------------------------------

export type MemoryLevel = "low" | "mid" | "high";

export interface MemoryLevelOption {
  level: MemoryLevel;
  label: string;
  importance: number;
}

/** 優先度の 3 段階（UI の「低 / 中 / 高」）と保存する importance */
export const MEMORY_LEVELS: readonly MemoryLevelOption[] = [
  { level: "low", label: "低", importance: 0.3 },
  { level: "mid", label: "中", importance: 0.6 },
  { level: "high", label: "高", importance: 0.9 },
] as const;

/** 記憶を追加するときの既定の優先度 */
export const DEFAULT_MEMORY_LEVEL: MemoryLevel = "mid";

/** 記憶の最大文字数（API: 1〜500 文字） */
export const MEMORY_CONTENT_MAX = 500;

export function levelToImportance(level: MemoryLevel): number {
  return MEMORY_LEVELS.find((option) => option.level === level)?.importance ?? 0.6;
}

/**
 * importance（0〜1）を最も近い段階に丸める。境界は中間点（0.45 / 0.75）で、ちょうど中間は上の段階。
 * 自動抽出された記憶（0.62 や 0.85 など）もここで表示用の段階に変換する。
 */
export function importanceToLevel(importance: number): MemoryLevel {
  if (!Number.isFinite(importance)) return "mid";
  // 浮動小数の誤差（0.45000000001 等）で段階が揺れないよう小数第 4 位で丸める
  const value = Math.round(importance * 10_000) / 10_000;
  if (value < 0.45) return "low";
  if (value < 0.75) return "mid";
  return "high";
}

export function levelLabel(level: MemoryLevel): string {
  return MEMORY_LEVELS.find((option) => option.level === level)?.label ?? "中";
}

// ---------------------------------------------------------------------------
// タグ
// ---------------------------------------------------------------------------

export function hasTag(memory: Pick<MemoryDTO, "tags">, tag: string): boolean {
  return memory.tags.includes(tag);
}

/** 「二人だけの秘密」 */
export function isSecretMemory(memory: Pick<MemoryDTO, "tags">): boolean {
  return hasTag(memory, MEMORY_TAG_SECRET);
}

/** 自動要約（中期メモリ） */
export function isSummaryMemory(memory: Pick<MemoryDTO, "tags">): boolean {
  return hasTag(memory, MEMORY_TAG_SUMMARY);
}

/** タグの付け外し（他のタグと順序は保つ・重複しない） */
export function withTag(tags: readonly string[], tag: string, enabled: boolean): string[] {
  const without = tags.filter((t) => t !== tag);
  return enabled ? [...without, tag] : without;
}

// ---------------------------------------------------------------------------
// 並び順・キャッシュ操作（純粋関数）
// ---------------------------------------------------------------------------

/** API と同じ並び（重要度の高い順 → 新しい順） */
export function sortMemories(memories: readonly MemoryDTO[]): MemoryDTO[] {
  return [...memories].sort(
    (a, b) => b.importance - a.importance || (a.created_at < b.created_at ? 1 : -1),
  );
}

/**
 * パネルを開いている間の表示順を安定させる。
 * 優先度を変えるたびに行が飛び回らないよう、既に表示している記憶は位置を保ち、
 * 新しく現れた記憶は先頭に（API の並び順で）追加する。消えた記憶は除く。
 */
export function stableMemoryOrder(
  previousOrder: readonly string[],
  memories: readonly MemoryDTO[],
): string[] {
  const present = new Set(memories.map((memory) => memory.id));
  const kept = previousOrder.filter((id) => present.has(id));
  const keptSet = new Set(kept);
  const added = memories.map((memory) => memory.id).filter((id) => !keptSet.has(id));
  return [...added, ...kept];
}

/** 1 件を部分更新（楽観的更新用）。ユーザーが編集した記憶は is_user_edited = true になる */
export function patchMemory(
  memories: readonly MemoryDTO[],
  memoryId: string,
  patch: UpdateMemoryRequest,
  now: string = new Date().toISOString(),
): MemoryDTO[] {
  return memories.map((memory) =>
    memory.id === memoryId
      ? {
          ...memory,
          ...(patch.content !== undefined ? { content: patch.content } : {}),
          ...(patch.importance !== undefined ? { importance: patch.importance } : {}),
          ...(patch.tags !== undefined ? { tags: [...patch.tags] } : {}),
          is_user_edited: true,
          updated_at: now,
        }
      : memory,
  );
}

export const TEMP_MEMORY_PREFIX = "temp-";

export function isTempMemoryId(id: string): boolean {
  return id.startsWith(TEMP_MEMORY_PREFIX);
}

/** 追加中（サーバー未保存）の仮の記憶 */
export function buildTempMemory(
  request: CreateMemoryRequest,
  tempId: string,
  now: string = new Date().toISOString(),
): MemoryDTO {
  return {
    id: tempId,
    character_id: request.character_id,
    content: request.content,
    importance: request.importance ?? 0.7,
    tags: [...(request.tags ?? [])],
    is_user_edited: true,
    source_message_id: null,
    created_at: now,
    updated_at: now,
  };
}

// ---------------------------------------------------------------------------
// React Query
// ---------------------------------------------------------------------------

const memoriesMutationKey = (characterId: string) =>
  [...queryKeys.memories(characterId), "mutation"] as const;

/** そのキャラが覚えていること（GET /memories?character_id=） */
export function useMemories(characterId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.memories(characterId),
    queryFn: async ({ signal }) => (await api.listMemories(characterId, { signal })).memories,
    enabled,
    // パネルを開くたびに最新化（会話で新しく覚えたことを反映する）
    staleTime: 0,
  });
}

interface MemoriesContext {
  previous: MemoryDTO[] | undefined;
}

/** 楽観的更新の共通処理: 進行中の取得を止めてスナップショットを取り、キャッシュを書き換える */
async function optimistic(
  queryClient: QueryClient,
  characterId: string,
  update: (memories: MemoryDTO[]) => MemoryDTO[],
): Promise<MemoriesContext> {
  const key = queryKeys.memories(characterId);
  await queryClient.cancelQueries({ queryKey: key });
  const previous = queryClient.getQueryData<MemoryDTO[]>(key);
  queryClient.setQueryData<MemoryDTO[]>(key, (memories) => update(memories ?? []));
  return { previous };
}

function rollback(queryClient: QueryClient, characterId: string, context?: MemoriesContext) {
  if (context) queryClient.setQueryData(queryKeys.memories(characterId), context.previous);
}

/** 最後のミューテーションが終わったときだけ再取得する（途中で古いサーバー状態に戻って見えるのを防ぐ） */
function settle(queryClient: QueryClient, characterId: string) {
  if (queryClient.isMutating({ mutationKey: memoriesMutationKey(characterId) }) === 1) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.memories(characterId) });
  }
}

let tempSeq = 0;

/** 記憶を追加（POST /memories） */
export function useCreateMemory(characterId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: memoriesMutationKey(characterId),
    mutationFn: ({ request }: { request: CreateMemoryRequest; tempId: string }) =>
      api.createMemory(request),
    onMutate: async ({ request, tempId }) =>
      optimistic(queryClient, characterId, (memories) => [
        buildTempMemory(request, tempId),
        ...memories,
      ]),
    onSuccess: (created, { tempId }) => {
      queryClient.setQueryData<MemoryDTO[]>(queryKeys.memories(characterId), (memories) =>
        (memories ?? []).map((memory) => (memory.id === tempId ? created : memory)),
      );
    },
    onError: (_error, _variables, context) => rollback(queryClient, characterId, context),
    onSettled: () => settle(queryClient, characterId),
  });
}

/** 仮 id を発行する（useCreateMemory の variables.tempId 用） */
export function nextTempMemoryId(): string {
  tempSeq += 1;
  return `${TEMP_MEMORY_PREFIX}${Date.now().toString(36)}-${tempSeq}`;
}

/** 記憶を更新（PATCH /memories/{id}）— 内容・優先度・タグ */
export function useUpdateMemory(characterId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: memoriesMutationKey(characterId),
    mutationFn: ({ memoryId, patch }: { memoryId: string; patch: UpdateMemoryRequest }) =>
      api.updateMemory(memoryId, patch),
    onMutate: async ({ memoryId, patch }) =>
      optimistic(queryClient, characterId, (memories) => patchMemory(memories, memoryId, patch)),
    onSuccess: (updated) => {
      queryClient.setQueryData<MemoryDTO[]>(queryKeys.memories(characterId), (memories) =>
        (memories ?? []).map((memory) => (memory.id === updated.id ? updated : memory)),
      );
    },
    onError: (_error, _variables, context) => rollback(queryClient, characterId, context),
    onSettled: () => settle(queryClient, characterId),
  });
}

/** 記憶を削除（DELETE /memories/{id}） */
export function useDeleteMemory(characterId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: memoriesMutationKey(characterId),
    mutationFn: ({ memoryId }: { memoryId: string }) => api.deleteMemory(memoryId),
    onMutate: async ({ memoryId }) =>
      optimistic(queryClient, characterId, (memories) =>
        memories.filter((memory) => memory.id !== memoryId),
      ),
    onError: (_error, _variables, context) => rollback(queryClient, characterId, context),
    onSettled: () => settle(queryClient, characterId),
  });
}
