/**
 * DM 一覧・会話のデータ層（C-1 / C-2）。
 *
 * - 一覧: RPC list_dm_threads()（RLS: 自分の会話のみ）。Realtime（自分の会話の messages INSERT）+ 30 秒ポーリング +
 *   フォーカス復帰で追従する。取得した未読数はタブバーの未読バッジ（queryKeys.dmUnreadTotal）にも反映する。
 * - 会話: 既にある会話は API を経由せずに開く（DM 一覧のキャッシュ → supabase-js で直接参照。RLS: 自分の会話のみ）。
 *   直接参照では最新 1 ページのメッセージも同時に取得し、メッセージのキャッシュを埋める（1 往復で表示できる）。
 *   無いときだけ Python API POST /conversations で作成する（初回はキャラの挨拶が 1 件保存される）。
 *   API が停止・再起動中でも、既存の会話の履歴は読める。
 * - 既読: RPC mark_conversation_read(p_conversation_id)。
 * - 一覧の行をタップし始めた時点で prefetchDmConversation() により会話画面のデータを先読みする。
 *
 * 注意: queryKeys.dm() は会話・メッセージのキャッシュも含むプレフィックス。DM 画面を開いたまま dm() を
 * invalidate すると全ページの再取得が走るため、一覧と未読バッジだけを更新する invalidateDmSummaries() を使う。
 */

import { PUBLIC_CHARACTER_COLUMNS, type DmThread, type PublicCharacter } from "@everkano/shared";
import { queryOptions, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { toAppError } from "@/lib/api/errors";
import { api } from "@/lib/api/client";
import { characterStateQueryOptions } from "@/lib/queries/character-state";
import { queryKeys } from "@/lib/queries/keys";
import {
  MESSAGE_COLUMNS,
  MESSAGES_PAGE_SIZE,
  messagesQueryOptions,
  seedMessagesCache,
  toMessagesPage,
  uniqueSuffix,
  type MessagesPage,
} from "@/lib/queries/messages";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";

export type { DmThread };

/** DM 一覧のポーリング間隔（Realtime の保険） */
export const DM_THREADS_REFETCH_MS = 30_000;

/** Realtime の postgres_changes フィルタ `in` に渡せる値の上限 */
export const REALTIME_IN_FILTER_MAX = 100;

/** 開いている DM の会話（表示に必要なのは id だけ） */
export interface DmConversationRef {
  id: string;
  character_id: string;
  /** この取得で新規作成された（キャラの挨拶が保存された） */
  created: boolean;
}

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

/**
 * DM 一覧の Realtime 購読のフィルタ（自分の会話の id に限定する）。
 *
 * フィルタ無しで messages を購読すると、全ユーザーのすべての発言について DM 一覧を開いている人数分の
 * RLS 判定が Realtime サーバーで走る（単一スレッドで処理され、会話画面の購読も遅れる）。
 * 一覧は新しい順なので、上限を超える場合は最近の会話を優先する（残りは 30 秒ポーリングで追従）。
 * 会話が無ければ null（購読しない）。id は並べ替えて、同じ集合なら同じ文字列にする（購読の張り直しを防ぐ）。
 */
export function dmThreadsRealtimeFilter(conversationIds: readonly string[]): string | null {
  const ids = [...new Set(conversationIds)].slice(0, REALTIME_IN_FILTER_MAX).sort();
  return ids.length > 0 ? `conversation_id=in.(${ids.join(",")})` : null;
}

/** DM 一覧の行 → 会話（API を経由せずに会話画面を開ける） */
export function conversationFromThreads(
  threads: readonly Pick<DmThread, "conversation_id" | "character_id">[] | undefined,
  characterId: string,
): DmConversationRef | undefined {
  const thread = threads?.find((t) => t.character_id === characterId);
  return thread
    ? { id: thread.conversation_id, character_id: thread.character_id, created: false }
    : undefined;
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
  if (error) throw toAppError(error);
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
 * 自分の会話への新着メッセージを購読して DM 一覧を更新する（conversationIds = 一覧に出ている会話）。
 * 連続で届いても 1 回にまとめる（300ms）。会話がまだ無ければ購読しない（ポーリングとフォーカス復帰で追従）。
 * 購読の張り直し（再接続）のときだけ取りこぼしを再取得する。最初の SUBSCRIBED は一覧を取得した直後
 * （このフックは取得済みの一覧から購読対象を決める）なので、再取得すると同じ RPC が 2 回続くだけになる。
 */
export function useDmThreadsRealtime(conversationIds: readonly string[]): void {
  const queryClient = useQueryClient();
  const filter = dmThreadsRealtimeFilter(conversationIds);
  useEffect(() => {
    if (!filter) return;
    const supabase = getSupabaseBrowserClient();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let subscribedOnce = false;
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(() => void invalidateDmSummaries(queryClient), 300);
    };
    const channel = supabase
      .channel(`dm-threads:${uniqueSuffix()}`)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "messages", filter },
        schedule,
      )
      .subscribe((status, error) => {
        if (status === "SUBSCRIBED") {
          if (subscribedOnce) schedule();
          subscribedOnce = true;
        } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          console.warn(`[dm] threads realtime ${status}`, error?.message ?? "");
        }
      });
    return () => {
      clearTimeout(timer);
      void supabase.removeChannel(channel);
    };
  }, [filter, queryClient]);
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
      if (error) throw toAppError(error);
      return data ?? [];
    },
    staleTime: 5 * 60_000,
  });
}

/** DM ヘッダー用のキャラ情報のクエリ設定（プリフェッチと共通） */
export function dmCharacterQueryOptions(characterId: string) {
  return queryOptions<
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
      if (error) throw toAppError(error);
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

/** DM ヘッダー用のキャラ情報。一覧のキャッシュがあれば即座に仮表示する */
export function useDmCharacter(characterId: string, enabled = true) {
  const queryClient = useQueryClient();
  return useQuery({
    ...dmCharacterQueryOptions(characterId),
    enabled,
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
 * 自分とそのキャラの会話を、最新 1 ページのメッセージと一緒に取得する（1 往復。RLS: 自分の会話のみ）。
 * 無い・キャラが公開終了（characters の RLS で見えない）なら null。
 */
export async function fetchOwnConversation(
  supabase: TypedSupabaseClient,
  characterId: string,
  signal?: AbortSignal,
): Promise<{ id: string; character_id: string; firstPage: MessagesPage } | null> {
  let query = supabase
    .from("conversations")
    .select(`id, character_id, characters!inner(id), messages(${MESSAGE_COLUMNS})`)
    .eq("character_id", characterId)
    .order("created_at", { referencedTable: "messages", ascending: false })
    .order("id", { referencedTable: "messages", ascending: false })
    .limit(MESSAGES_PAGE_SIZE, { referencedTable: "messages" });
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query.maybeSingle();
  if (error) throw toAppError(error);
  if (!data) return null;
  return {
    id: data.id,
    character_id: data.character_id,
    firstPage: toMessagesPage(data.messages),
  };
}

/**
 * 会話を開く: 既存の会話は supabase-js で直接読み（最新ページでメッセージのキャッシュも埋める）、
 * 無いときだけ POST /conversations で作成する（冪等。初回はキャラの挨拶が 1 件保存される）。
 * キャラが公開終了していれば API が 404 を返す（画面は「アカウントが見つかりません」）。
 */
export async function resolveConversation(
  queryClient: QueryClient,
  characterId: string,
  signal?: AbortSignal,
): Promise<DmConversationRef> {
  const existing = await fetchOwnConversation(getSupabaseBrowserClient(), characterId, signal);
  if (existing) {
    seedMessagesCache(queryClient, existing.id, existing.firstPage);
    return { id: existing.id, character_id: existing.character_id, created: false };
  }
  const response = await api.createConversation({ character_id: characterId }, { signal });
  const { conversation, created, greeting_message: greeting } = response;
  if (created) {
    void invalidateDmSummaries(queryClient);
    // 作成直後の会話は挨拶 1 件だけ（以降の発言は Realtime / 差分取得で追従する）
    if (greeting) seedMessagesCache(queryClient, conversation.id, toMessagesPage([greeting]));
  }
  return { id: conversation.id, character_id: conversation.character_id, created };
}

/**
 * (自分, キャラ) の会話。DM 一覧のキャッシュにあれば通信せずに即座に表示を始める（会話の id は変わらない）。
 * 新規作成時（挨拶メッセージが保存された）は DM 一覧を更新する。
 */
export function useConversation(characterId: string, enabled = true) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.conversation(characterId),
    queryFn: ({ signal }) => resolveConversation(queryClient, characterId, signal),
    initialData: () =>
      conversationFromThreads(
        queryClient.getQueryData<DmThread[]>(queryKeys.dmThreads()),
        characterId,
      ),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 30 * 60_000,
  });
}

/**
 * DM 会話画面のデータを先読みする（一覧の行に触れた時点で呼ぶ。遷移・画面の読み込みと並行して取得が進み、
 * 画面側の useQuery は進行中の取得・取得済みのキャッシュをそのまま使う）。
 * 会話の作成（POST /conversations）は副作用があるため先読みしない（一覧にある既存の会話だけ）。
 */
export function prefetchDmConversation(queryClient: QueryClient, characterId: string): void {
  void queryClient.prefetchQuery(dmCharacterQueryOptions(characterId));
  // ヘッダーの今の状況（character_states）
  void queryClient.prefetchQuery(characterStateQueryOptions(characterId));
  const conversation =
    queryClient.getQueryData<DmConversationRef>(queryKeys.conversation(characterId)) ??
    conversationFromThreads(
      queryClient.getQueryData<DmThread[]>(queryKeys.dmThreads()),
      characterId,
    );
  if (conversation) void queryClient.prefetchInfiniteQuery(messagesQueryOptions(conversation.id));
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
