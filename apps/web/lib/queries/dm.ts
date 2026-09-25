/**
 * DM 一覧・会話のデータ層（C-1 / C-2）。
 *
 * - 一覧: RPC list_dm_threads()（RLS: 自分の会話のみ）。Realtime（messages INSERT）+ 30 秒ポーリング +
 *   フォーカス復帰で追従する。取得した未読数はタブバーの未読バッジ（queryKeys.dmUnreadTotal）にも反映する。
 * - 会話: Python API POST /conversations（取得または作成。初回はキャラの挨拶が 1 件保存される）。
 * - 既読: RPC mark_conversation_read(p_conversation_id)。
 *
 * 注意: queryKeys.dm() は会話・メッセージのキャッシュも含むプレフィックス。DM 画面を開いたまま dm() を
 * invalidate すると全ページの再取得が走るため、一覧と未読バッジだけを更新する invalidateDmSummaries() を使う。
 */

import {
  PUBLIC_CHARACTER_COLUMNS,
  type CreateConversationResponse,
  type DmThread,
  type PublicCharacter,
} from "@everkano/shared";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/queries/keys";
import { uniqueSuffix } from "@/lib/queries/messages";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export type { DmThread };

/** DM 一覧のポーリング間隔（Realtime の保険） */
export const DM_THREADS_REFETCH_MS = 30_000;

/** おすすめ（まだ会話していないキャラ）のクエリキー */
export const dmSuggestionsKey = () => [...queryKeys.dm(), "suggestions"] as const;

/** DM ヘッダー用のキャラ情報のクエリキー（他機能の characterById と形が衝突しないよう派生させる） */
export const dmCharacterKey = (characterId: string) =>
  [...queryKeys.characterById(characterId), "public"] as const;

// ---------------------------------------------------------------------------
// 純粋関数
// ---------------------------------------------------------------------------

/** 未読数の合計（タブバーのバッジ） */
export function totalUnread(threads: readonly Pick<DmThread, "unread_count">[]): number {
  return threads.reduce((sum, thread) => sum + Math.max(0, thread.unread_count ?? 0), 0);
}

/** 一覧のプレビュー文（自分の発言は「あなた: 」を付ける。改行は空白に） */
export function threadPreview(
  thread: Pick<DmThread, "last_message_body" | "last_message_sender_type">,
): string {
  const body = (thread.last_message_body ?? "").replace(/\s+/g, " ").trim();
  if (!body) return "メッセージを送信しよう";
  return thread.last_message_sender_type === "user" ? `あなた: ${body}` : body;
}

/** 一覧の絞り込み（名前・ハンドルの部分一致、大文字小文字を区別しない） */
export function filterThreads<T extends Pick<DmThread, "character_name" | "character_handle">>(
  threads: readonly T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...threads];
  return threads.filter(
    (thread) =>
      thread.character_name.toLowerCase().includes(q) ||
      thread.character_handle.toLowerCase().includes(q),
  );
}

/** まだ会話していないキャラだけを残す（おすすめ） */
export function excludeConversed<T extends Pick<PublicCharacter, "id">>(
  characters: readonly T[],
  threads: readonly Pick<DmThread, "character_id">[],
): T[] {
  const conversed = new Set(threads.map((thread) => thread.character_id));
  return characters.filter((character) => !conversed.has(character.id));
}

/** 会話を既読にしたとして一覧キャッシュを更新する（楽観的更新） */
export function markThreadReadInList(
  threads: readonly DmThread[] | undefined,
  conversationId: string,
): DmThread[] | undefined {
  if (!threads) return threads;
  let changed = false;
  const next = threads.map((thread) => {
    if (thread.conversation_id !== conversationId || thread.unread_count === 0) return thread;
    changed = true;
    return { ...thread, unread_count: 0 };
  });
  return changed ? next : (threads as DmThread[]);
}

// ---------------------------------------------------------------------------
// 取得・更新
// ---------------------------------------------------------------------------

export async function fetchDmThreads(): Promise<DmThread[]> {
  const { data, error } = await getSupabaseBrowserClient().rpc("list_dm_threads");
  if (error) throw error;
  return data ?? [];
}

/** DM 一覧とタブバーの未読バッジを再取得する */
export function invalidateDmSummaries(queryClient: QueryClient): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.dmThreads() }),
    queryClient.invalidateQueries({ queryKey: queryKeys.dmUnreadTotal() }),
  ]).then(() => undefined);
}

/** DM 一覧（RPC list_dm_threads）。取得結果の未読合計はタブバーのバッジにも反映する */
export function useDmThreads() {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.dmThreads(),
    queryFn: async () => {
      const threads = await fetchDmThreads();
      queryClient.setQueryData(queryKeys.dmUnreadTotal(), totalUnread(threads));
      return threads;
    },
    staleTime: 10_000,
    refetchInterval: DM_THREADS_REFETCH_MS,
    refetchOnWindowFocus: true,
  });
}

/**
 * 自分の会話への新着メッセージを購読して DM 一覧を更新する（RLS により自分の会話の行だけが届く）。
 * 連続で届いても 1 回にまとめる（300ms）。
 */
export function useDmThreadsRealtime(): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(() => void invalidateDmSummaries(queryClient), 300);
    };
    const channel = supabase
      .channel(`dm-threads:${uniqueSuffix()}`)
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "messages" }, schedule)
      .subscribe((status, error) => {
        if (status === "SUBSCRIBED") schedule();
        else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          console.warn(`[dm] threads realtime ${status}`, error?.message ?? "");
        }
      });
    return () => {
      clearTimeout(timer);
      void supabase.removeChannel(channel);
    };
  }, [queryClient]);
}

/** おすすめ: 有効なキャラ（フォロワー数順）。一覧側で会話済みを除外する */
export function useSuggestedCharacters() {
  return useQuery({
    queryKey: dmSuggestionsKey(),
    queryFn: async ({ signal }): Promise<PublicCharacter[]> => {
      const { data, error } = await getSupabaseBrowserClient()
        .from("characters")
        .select(PUBLIC_CHARACTER_COLUMNS)
        .eq("is_active", true)
        .order("follower_count", { ascending: false })
        .limit(30)
        .abortSignal(signal);
      if (error) throw error;
      return data ?? [];
    },
    staleTime: 5 * 60_000,
  });
}

/** DM ヘッダー用のキャラ情報。一覧のキャッシュがあれば即座に仮表示する */
export function useDmCharacter(characterId: string, enabled = true) {
  const queryClient = useQueryClient();
  return useQuery<
    PublicCharacter | null,
    Error,
    PublicCharacter | null,
    ReturnType<typeof dmCharacterKey>
  >({
    queryKey: dmCharacterKey(characterId),
    queryFn: async ({ signal }): Promise<PublicCharacter | null> => {
      const { data, error } = await getSupabaseBrowserClient()
        .from("characters")
        .select(PUBLIC_CHARACTER_COLUMNS)
        .eq("id", characterId)
        .abortSignal(signal)
        .maybeSingle();
      if (error) throw error;
      return data;
    },
    enabled,
    staleTime: 5 * 60_000,
    placeholderData: (): PublicCharacter | undefined => {
      const suggestion = queryClient
        .getQueryData<PublicCharacter[]>(dmSuggestionsKey())
        ?.find((character) => character.id === characterId);
      if (suggestion) return suggestion;
      const thread = queryClient
        .getQueryData<DmThread[]>(queryKeys.dmThreads())
        ?.find((t) => t.character_id === characterId);
      if (!thread) return undefined;
      return {
        id: thread.character_id,
        handle: thread.character_handle,
        name: thread.character_name,
        avatar_url: thread.character_avatar_url,
        bio: null,
        follower_count: 0,
        is_active: true,
        created_at: thread.last_message_at,
      };
    },
  });
}

/**
 * (自分, キャラ) の会話を取得または作成する（POST /conversations は冪等）。
 * 新規作成時（挨拶メッセージが保存された）は DM 一覧を更新する。
 */
export function useConversation(characterId: string, enabled = true) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.conversation(characterId),
    queryFn: async ({ signal }): Promise<CreateConversationResponse> => {
      const response = await api.createConversation({ character_id: characterId }, { signal });
      if (response.created) void invalidateDmSummaries(queryClient);
      return response;
    },
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 30 * 60_000,
  });
}

/**
 * 会話を既読にする関数を返す（RPC mark_conversation_read）。
 * - 一覧キャッシュの未読数を即座に 0 にしてバッジを更新し、完了後に再取得する
 * - 実行中に再度呼ばれたら、完了後にもう 1 回だけ実行する（連続呼び出しをまとめる）
 */
export function useMarkConversationRead(conversationId: string | undefined): () => void {
  const queryClient = useQueryClient();
  const state = useRef<{ running: boolean; again: boolean }>({ running: false, again: false });

  return useCallback(() => {
    if (!conversationId) return;
    if (state.current.running) {
      state.current.again = true;
      return;
    }
    state.current.running = true;

    queryClient.setQueryData<DmThread[]>(queryKeys.dmThreads(), (threads) =>
      markThreadReadInList(threads, conversationId),
    );
    const threads = queryClient.getQueryData<DmThread[]>(queryKeys.dmThreads());
    if (threads) queryClient.setQueryData(queryKeys.dmUnreadTotal(), totalUnread(threads));

    const run = async () => {
      try {
        const { error } = await getSupabaseBrowserClient().rpc("mark_conversation_read", {
          p_conversation_id: conversationId,
        });
        if (error) console.warn("[dm] mark_conversation_read failed:", error.message);
      } catch (error) {
        console.warn("[dm] mark_conversation_read failed:", error);
      } finally {
        await invalidateDmSummaries(queryClient);
        if (state.current.again) {
          state.current.again = false;
          await run();
        } else {
          state.current.running = false;
        }
      }
    };
    void run();
  }, [conversationId, queryClient]);
}
