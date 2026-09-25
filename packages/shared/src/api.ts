/**
 * Python API（apps/api, FastAPI）の入出力型。
 *
 * - ここが Web 側から見た API 契約の単一の正（Single Source of Truth）。
 * - Python 側は apps/api/app/models/*.py の Pydantic モデルが同じ形を持つ。
 * - 乖離は `pnpm --filter @everkano/shared check:api`（OpenAPI 突合）と
 *   apps/api/tests/test_openapi_contract.py で検知する。
 * - JSON のキーはすべて snake_case。日時は ISO 8601 文字列（UTC）。
 */

export type UUID = string;
export type ISODateString = string;

// ---------------------------------------------------------------------------
// 共通
// ---------------------------------------------------------------------------

export type SenderType = "user" | "character";
export type AuthorType = "user" | "character";

/** エラーコード。HTTPステータスと対応する。 */
export type ApiErrorCode =
  | "unauthorized" // 401: JWT 無効・期限切れ
  | "forbidden" // 403: 他人のリソース
  | "account_deleted" // 403: 退会済みユーザー
  | "not_found" // 404
  | "validation_error" // 422: 入力不正
  | "moderation_blocked" // 422: コメント投稿が Gate #1 で拒否された
  | "rate_limited" // 429: レート制限（Retry-After ヘッダ付き）
  | "llm_unavailable" // 503: LLM プロバイダ障害
  | "internal_error"; // 500

export interface ApiErrorBody {
  error: {
    code: ApiErrorCode;
    /** ユーザーにそのまま表示できる日本語メッセージ */
    message: string;
    request_id?: string;
  };
}

// ---------------------------------------------------------------------------
// GET /health（認証不要）
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  env: string;
  llm_mode: "live" | "mock";
  embedding_mode: "live" | "hash";
  db: "ok" | "error";
}

// ---------------------------------------------------------------------------
// DM
// ---------------------------------------------------------------------------

export interface ConversationDTO {
  id: UUID;
  user_id: UUID;
  character_id: UUID;
  last_message_at: ISODateString;
  user_last_read_at: ISODateString;
  created_at: ISODateString;
}

export interface MessageDTO {
  id: UUID;
  conversation_id: UUID;
  sender_type: SenderType;
  body: string;
  created_at: ISODateString;
}

/** POST /conversations — 会話の取得または作成（初回はキャラの挨拶メッセージを1件保存） */
export interface CreateConversationRequest {
  character_id: UUID;
}

export interface CreateConversationResponse {
  conversation: ConversationDTO;
  created: boolean;
  /** 新規作成時にキャラが送る最初のメッセージ（既存会話なら null） */
  greeting_message: MessageDTO | null;
}

/** POST /chat — DM のキャラ返答を生成 */
export interface ChatRequest {
  character_id: UUID;
  conversation_id: UUID;
  /** 1〜2000文字 */
  message: string;
}

export interface ChatResponse {
  /** キャラ返答メッセージの ID（= character_message.id） */
  message_id: UUID;
  reply: string;
  memories_used: UUID[];
  memories_created: UUID[];
  /** [追加] 保存されたユーザー発言（楽観的更新の置き換え用） */
  user_message: MessageDTO;
  /** [追加] 保存されたキャラ返答 */
  character_message: MessageDTO;
  /** [追加] Gate #1 で入力または出力が差し止められ定型文が返った場合 true */
  moderated: boolean;
}

// ---------------------------------------------------------------------------
// メモリ（§9.4）
// ---------------------------------------------------------------------------

/** 既知のタグ。自由入力タグも許容する（string）。 */
export const MEMORY_TAG_SECRET = "secret"; // 「二人だけの秘密」
export const MEMORY_TAG_SUMMARY = "summary"; // 中期メモリ（自動要約）

export interface MemoryDTO {
  id: UUID;
  character_id: UUID;
  content: string;
  /** 0.00〜1.00 */
  importance: number;
  tags: string[];
  is_user_edited: boolean;
  source_message_id: UUID | null;
  created_at: ISODateString;
  updated_at: ISODateString;
}

/** GET /memories?character_id= */
export interface ListMemoriesResponse {
  memories: MemoryDTO[];
}

/** POST /memories — ユーザーが記憶を追加（is_user_edited = true） */
export interface CreateMemoryRequest {
  character_id: UUID;
  /** 1〜500文字 */
  content: string;
  /** 省略時 0.7 */
  importance?: number;
  tags?: string[];
}

/** PATCH /memories/{id} — 内容・重要度・タグ更新（is_user_edited = true） */
export interface UpdateMemoryRequest {
  content?: string;
  importance?: number;
  tags?: string[];
}

// DELETE /memories/{id} -> 204 No Content

// ---------------------------------------------------------------------------
// コメント
// ---------------------------------------------------------------------------

export interface CommentDTO {
  id: UUID;
  post_id: UUID;
  parent_comment_id: UUID | null;
  author_type: AuthorType;
  author_user_id: UUID | null;
  author_character_id: UUID | null;
  body: string;
  created_at: ISODateString;
}

/** POST /comments — ユーザーコメント投稿（Gate #1 を通過したもののみ保存） */
export interface CreateCommentRequest {
  post_id: UUID;
  /** 1〜500文字 */
  body: string;
  parent_comment_id?: UUID | null;
}

export interface CreateCommentResponse {
  comment: CommentDTO;
  /** 投稿者キャラの自動返信がバックグラウンドで予約されたか（Realtime で届く） */
  reply_scheduled: boolean;
}

/** POST /comments/generate — 投稿者キャラがコメントに返信（§7） */
export interface GenerateCommentRequest {
  post_id: UUID;
  parent_comment_id: UUID;
}

export interface GenerateCommentResponse {
  /** 出力モデレーションで差し止められた場合は null */
  comment: CommentDTO | null;
}
