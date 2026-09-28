import type { Database } from "./database.types";

type PublicTables = Database["public"]["Tables"];

export type Tables<T extends keyof PublicTables> = PublicTables[T]["Row"];

export type ProfileRow = Tables<"profiles">;
export type PostRow = Tables<"posts">;
export type CommentRow = Tables<"comments">;
export type ConversationRow = Tables<"conversations">;
export type MessageRow = Tables<"messages">;
export type MemoryRow = Tables<"memories">;

/**
 * クライアントが参照可能なキャラクター列。
 * system_prompt / persona_key は列権限で非公開のため select に含めないこと。
 */
export type PublicCharacter = Pick<
  Tables<"characters">,
  "id" | "handle" | "name" | "avatar_url" | "bio" | "follower_count" | "is_active" | "created_at"
>;

/** supabase-js の select 文字列（キャラクターの公開列）。`select('*')` は列権限エラーになる。 */
export const PUBLIC_CHARACTER_COLUMNS =
  "id, handle, name, avatar_url, bio, follower_count, is_active, created_at" as const;

/** クライアントが参照可能なメモリ列（embedding は非公開）。 */
export const PUBLIC_MEMORY_COLUMNS =
  "id, user_id, character_id, content, importance, tags, source_message_id, is_user_edited, created_at, updated_at" as const;

type DmThreadRow = Database["public"]["Functions"]["list_dm_threads"]["Returns"][number];

/**
 * DM 一覧の行（RPC list_dm_threads）。
 * 最後のメッセージは left join lateral なので、メッセージが無い会話（作成直後など）では null になる。
 * 生成された型（returns table の text 列）は null を表せないため、ここで正しく狭める。
 */
export type DmThread = Omit<DmThreadRow, "last_message_body" | "last_message_sender_type"> & {
  last_message_body: string | null;
  last_message_sender_type: string | null;
};
