/**
 * Python API（apps/api, FastAPI）の入出力型。
 *
 * - ここが Web 側から見た API 契約の単一の正（Single Source of Truth）。
 * - Python 側は apps/api/app/models/*.py の Pydantic モデルが同じ形を持つ。
 * - 乖離は apps/api/tests/test_openapi_contract.py（Pydantic ↔ この型のフィールド突合）と
 *   `pnpm --filter @everkano/api openapi:check`（docs/api/openapi.json の更新漏れ）で検知する。
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
  /** [エンジン v1.0] キャラからの自発メッセージ（返答ではない） */
  is_proactive: boolean;
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
  /**
   * [エンジン v1.0] 自傷・希死念慮のシグナルを検知し、安全対応（相談窓口の案内）を優先した場合の情報（E6）。
   * 通常は null。Web は返答の下に相談窓口のカードを表示する。
   */
  safety: SafetyInfo | null;
}

/** E6: 相談窓口 */
export interface SafetyResource {
  name: string;
  phone: string | null;
  hours: string | null;
  url: string | null;
}

export interface SafetyInfo {
  triggered: boolean;
  resources: SafetyResource[];
}

/**
 * POST /chat/stream — /chat と同じ入力で、返答を Server-Sent Events で順に返す（E8: 最初の文字を早く表示する）。
 * レスポンスは `text/event-stream`。各イベントは `event: <type>` と `data: <JSON>`。
 * - `delta`   : 返答の続き（文単位。Gate #1 などの出力検査を通過した部分だけ）。表示中の吹き出しに追記する
 * - `replace` : 出力検査で差し止めた場合など、表示中の吹き出しの本文を置き換える
 * - `done`    : 保存が終わった最終結果（ChatResponse）。楽観的な吹き出しを保存済みのメッセージに置き換える
 * - `error`   : 失敗（ApiErrorBody["error"]）。何も保存していない。直後に接続が閉じる
 * 認証・所有者・レート制限のエラーはストリーム開始前に通常の JSON エラー（HTTP ステータス）で返る。
 * 記憶の抽出・好感度の更新は返答の後に非同期で行うため、`done` の memories_created は常に空。
 * 新しい記憶は Realtime（memories の INSERT）で届く。
 */
export type ChatStreamEvent =
  | { type: "delta"; data: { text: string } }
  | { type: "replace"; data: { text: string; reason: "moderated" | "safety" } }
  | { type: "done"; data: ChatResponse }
  | { type: "error"; data: ApiErrorBody["error"] };

// ---------------------------------------------------------------------------
// メモリ（§9.4）
// ---------------------------------------------------------------------------

/** [エンジン v1.0] 記憶の種類（M2） */
export type MemoryKind =
  | "fact" // 事実（仕事・住まい・家族など）
  | "preference" // 好み
  | "episode" // 出来事・エピソード
  | "promise" // 約束・予定
  | "emotion" // 感情（悩み・喜び）
  | "relationship" // 関係性の変化（呼び方・距離感）
  | "summary"; // 中期要約（自動）

/** active = 有効 / superseded = 新しい情報で置き換えられた（履歴。M4） */
export type MemoryStatus = "active" | "superseded";

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
  /** [エンジン v1.0] */
  kind: MemoryKind;
  status: MemoryStatus;
  /** 置き換えた新しい記憶（status = superseded のとき） */
  superseded_by: UUID | null;
  superseded_at: ISODateString | null;
  last_referenced_at: ISODateString | null;
  reference_count: number;
}

/**
 * GET /memories?character_id=&include_superseded=
 * 既定は有効な記憶だけ。include_superseded=true で置き換えられた古い記憶（履歴）も返す。
 */
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
  /** [エンジン v1.0] 省略時 "fact"。"summary" は指定できない */
  kind?: MemoryKind;
}

/** PATCH /memories/{id} — 内容・重要度・タグ更新（is_user_edited = true） */
export interface UpdateMemoryRequest {
  content?: string;
  importance?: number;
  tags?: string[];
  /** [エンジン v1.0] "summary" は指定できない */
  kind?: MemoryKind;
}

// DELETE /memories/{id} -> 204 No Content
// （削除した記憶は内容を持たない「墓標」になり、自動抽出で同じ記憶が復活しない。E5）

// ---------------------------------------------------------------------------
// 約束（エンジン v1.0 §4 M6 / §5 C8）
// ---------------------------------------------------------------------------

export type PromiseStatus = "pending" | "mentioned" | "done" | "cancelled";
export type DuePrecision = "datetime" | "day" | "week" | "month" | "unknown";

export interface PromiseDTO {
  id: UUID;
  character_id: UUID;
  content: string;
  due_at: ISODateString | null;
  due_precision: DuePrecision;
  /** pending = 未達 / mentioned = キャラが話題にした / done = 完了 / cancelled = 取り消し */
  status: PromiseStatus;
  created_at: ISODateString;
  updated_at: ISODateString;
}

/** GET /promises?character_id=&include_closed= （既定は pending / mentioned だけ） */
export interface ListPromisesResponse {
  promises: PromiseDTO[];
}

/** PATCH /promises/{id} — ユーザーによる完了・取り消し */
export interface UpdatePromiseRequest {
  status: "done" | "cancelled";
}

// ---------------------------------------------------------------------------
// 自発メッセージの設定（エンジン v1.0 §7 / E4）
// ---------------------------------------------------------------------------

export interface ProactiveGlobalSettings {
  /** false ならすべてのキャラから自発メッセージを受け取らない */
  enabled: boolean;
  /** 送らない時間帯（JST の時, 0〜23）。start == end なら時間帯の制限なし */
  quiet_start: number;
  quiet_end: number;
}

export interface ProactiveCharacterSetting {
  character_id: UUID;
  enabled: boolean;
}

/** GET /proactive/settings */
export interface ProactiveSettingsResponse {
  global: ProactiveGlobalSettings;
  /** キャラ別の設定（行が無いキャラは enabled = true 扱い） */
  characters: ProactiveCharacterSetting[];
}

/** PUT /proactive/settings — 全体設定の更新（省略した項目は変更しない） */
export interface UpdateProactiveGlobalSettingsRequest {
  enabled?: boolean;
  quiet_start?: number;
  quiet_end?: number;
}

/** PUT /proactive/settings/{character_id} */
export interface UpdateProactiveCharacterSettingRequest {
  enabled: boolean;
}

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
