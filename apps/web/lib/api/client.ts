import { isAuthRetryableFetchError } from "@supabase/supabase-js";
import type {
  ChatRequest,
  ChatResponse,
  CreateCommentRequest,
  CreateCommentResponse,
  CreateConversationRequest,
  CreateConversationResponse,
  CreateMemoryRequest,
  HealthResponse,
  ListMemoriesResponse,
  MemoryDTO,
  UpdateMemoryRequest,
  UUID,
} from "@everkano/shared";
import { getPublicEnv } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { API_ERROR_MESSAGES, ApiError, apiErrorFromResponse } from "./errors";

/**
 * Python API（apps/api / FastAPI）の型付きクライアント。
 *
 * 型は packages/shared/src/api.ts が単一の正。すべての認証付きエンドポイントに
 * Supabase のアクセストークンを `Authorization: Bearer` で付与する。
 * 失敗時は必ず ApiError（日本語 message 付き）を投げる。
 *
 * 使い方（クライアントコンポーネント）:
 *   import { api } from "@/lib/api/client";   // または "@/lib/api"
 *   const res = await api.sendChat({ character_id, conversation_id, message });
 *
 * React Query と併用する場合は queryFn の signal を渡す:
 *   queryFn: ({ signal }) => api.listMemories(characterId, { signal })
 */

export const DEFAULT_TIMEOUT_MS = 15_000;
/**
 * /chat のタイムアウト。API 側の CHAT_DEADLINE_SECONDS（既定 38 秒。超過時は何も保存せず 503）＋
 * 記憶の保存時間より長くしておくこと。短いと、サーバーでは保存済みの発言を再送して二重送信になる。
 */
export const CHAT_TIMEOUT_MS = 45_000;

export interface RequestOptions {
  /** 呼び出し側からの中断（React Query の signal など） */
  signal?: AbortSignal;
  /** タイムアウト（ミリ秒）を上書き */
  timeoutMs?: number;
}

export interface ApiClientConfig {
  /** 例: http://localhost:8000（末尾スラッシュ不要） */
  baseUrl: string;
  /** 現在のアクセストークン。未ログインなら null（一時的な失敗は ApiError を投げてよい） */
  getAccessToken: () => Promise<string | null>;
  /** テスト用に差し替え可能な fetch */
  fetch?: typeof fetch;
  defaultTimeoutMs?: number;
  chatTimeoutMs?: number;
  /** X-Request-ID を生成する（既定: crypto.randomUUID） */
  generateRequestId?: () => string;
}

type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";

interface CallOptions extends RequestOptions {
  method: HttpMethod;
  path: string;
  body?: unknown;
  query?: Record<string, string>;
  auth?: boolean;
  defaultTimeoutMs: number;
}

function defaultRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

export function createApiClient(config: ApiClientConfig) {
  const baseUrl = config.baseUrl.replace(/\/+$/, "");
  const doFetch =
    config.fetch ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));
  const defaultTimeoutMs = config.defaultTimeoutMs ?? DEFAULT_TIMEOUT_MS;
  const chatTimeoutMs = config.chatTimeoutMs ?? CHAT_TIMEOUT_MS;
  const generateRequestId = config.generateRequestId ?? defaultRequestId;

  async function call<T>(options: CallOptions): Promise<T> {
    const { method, path, body, query, auth = true, signal } = options;
    const timeoutMs = options.timeoutMs ?? options.defaultTimeoutMs;

    if (signal?.aborted) {
      throw new ApiError({ status: 0, code: "aborted", message: API_ERROR_MESSAGES.aborted });
    }

    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Request-ID": generateRequestId(),
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";

    if (auth) {
      const token = await config.getAccessToken();
      if (!token) {
        throw new ApiError({
          status: 401,
          code: "unauthorized",
          message: API_ERROR_MESSAGES.unauthorized,
        });
      }
      headers.Authorization = `Bearer ${token}`;
    }

    const url = new URL(`${baseUrl}${path}`);
    if (query) {
      for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
    }

    // タイムアウトと呼び出し側の中断を 1 つの AbortController にまとめる
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onExternalAbort = () => controller.abort();
    signal?.addEventListener("abort", onExternalAbort, { once: true });

    let response: Response;
    try {
      response = await doFetch(url.toString(), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      });
    } catch (cause) {
      if (timedOut) {
        throw new ApiError({
          status: 0,
          code: "timeout",
          message: API_ERROR_MESSAGES.timeout,
          requestId: headers["X-Request-ID"],
          cause,
        });
      }
      if (signal?.aborted) {
        throw new ApiError({
          status: 0,
          code: "aborted",
          message: API_ERROR_MESSAGES.aborted,
          cause,
        });
      }
      throw new ApiError({
        status: 0,
        code: "network_error",
        message: API_ERROR_MESSAGES.network_error,
        requestId: headers["X-Request-ID"],
        cause,
      });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onExternalAbort);
    }

    if (response.status === 204) {
      return undefined as T;
    }

    let parsed: unknown = undefined;
    const text = await response.text().catch(() => "");
    if (text) {
      try {
        parsed = JSON.parse(text) as unknown;
      } catch {
        parsed = undefined;
      }
    }

    if (!response.ok) {
      const error = apiErrorFromResponse(
        response.status,
        parsed,
        response.headers,
        headers["X-Request-ID"],
      );
      console.warn(
        `[api] ${method} ${path} -> ${response.status} ${error.code} (request_id=${error.requestId ?? "-"})`,
      );
      throw error;
    }

    if (parsed === undefined) {
      throw new ApiError({
        status: response.status,
        code: "internal_error",
        message: API_ERROR_MESSAGES.internal_error,
        requestId: response.headers.get("X-Request-ID") ?? undefined,
      });
    }
    return parsed as T;
  }

  return {
    /** GET /health（認証不要） */
    health(options: RequestOptions = {}): Promise<HealthResponse> {
      return call<HealthResponse>({
        ...options,
        method: "GET",
        path: "/health",
        auth: false,
        defaultTimeoutMs,
      });
    },

    /** POST /conversations — (ユーザー, キャラ) の会話を取得または作成（初回は挨拶メッセージ付き） */
    createConversation(
      body: CreateConversationRequest,
      options: RequestOptions = {},
    ): Promise<CreateConversationResponse> {
      return call<CreateConversationResponse>({
        ...options,
        method: "POST",
        path: "/conversations",
        body,
        defaultTimeoutMs,
      });
    },

    /** POST /chat — DM 送信 → キャラの返答（タイムアウト 45 秒） */
    sendChat(body: ChatRequest, options: RequestOptions = {}): Promise<ChatResponse> {
      return call<ChatResponse>({
        ...options,
        method: "POST",
        path: "/chat",
        body,
        defaultTimeoutMs: chatTimeoutMs,
      });
    },

    /** GET /memories?character_id= — そのキャラとの記憶一覧 */
    listMemories(characterId: UUID, options: RequestOptions = {}): Promise<ListMemoriesResponse> {
      return call<ListMemoriesResponse>({
        ...options,
        method: "GET",
        path: "/memories",
        query: { character_id: characterId },
        defaultTimeoutMs,
      });
    },

    /** POST /memories — 記憶を追加（is_user_edited = true） */
    createMemory(body: CreateMemoryRequest, options: RequestOptions = {}): Promise<MemoryDTO> {
      return call<MemoryDTO>({
        ...options,
        method: "POST",
        path: "/memories",
        body,
        defaultTimeoutMs,
      });
    },

    /** PATCH /memories/{id} — 内容・重要度・タグを更新 */
    updateMemory(
      memoryId: UUID,
      body: UpdateMemoryRequest,
      options: RequestOptions = {},
    ): Promise<MemoryDTO> {
      return call<MemoryDTO>({
        ...options,
        method: "PATCH",
        path: `/memories/${encodeURIComponent(memoryId)}`,
        body,
        defaultTimeoutMs,
      });
    },

    /** DELETE /memories/{id}（204） */
    deleteMemory(memoryId: UUID, options: RequestOptions = {}): Promise<void> {
      return call<void>({
        ...options,
        method: "DELETE",
        path: `/memories/${encodeURIComponent(memoryId)}`,
        defaultTimeoutMs,
      });
    },

    /** POST /comments — コメント投稿（Gate #1 で拒否されると 422 moderation_blocked） */
    createComment(
      body: CreateCommentRequest,
      options: RequestOptions = {},
    ): Promise<CreateCommentResponse> {
      return call<CreateCommentResponse>({
        ...options,
        method: "POST",
        path: "/comments",
        body,
        defaultTimeoutMs,
      });
    },

    // POST /comments/generate（投稿者キャラの返信を即時生成）は Web からは使わない。
    // 自動返信は POST /comments がサーバー側でスケジュールする（ADR-0014）。必要になったらここに追加する。
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

// ---------------------------------------------------------------------------
// 既定クライアント（ブラウザ専用: Supabase のブラウザセッションからアクセストークンを取得）
// import しただけでは環境変数を読まず、初回呼び出し時に生成する。
// ---------------------------------------------------------------------------

/** getSession() の結果（テストで差し替えるため最小限の形） */
export interface SessionReader {
  getSession(): Promise<{
    data: { session: { access_token: string } | null };
    error: { name?: string; status?: number | undefined; message: string } | null;
  }>;
}

/**
 * Supabase のセッションから API に付けるアクセストークンを読む（期限切れ間近ならリフレッシュしてから返す）。
 * - セッションが無い・リフレッシュトークンが無効（4xx）→ null（呼び出し側は 401 unauthorized → ログイン画面へ）
 * - リフレッシュが通信失敗・Auth の 5xx で失敗した → network_error を投げる。
 *   アクセストークンの期限が切れた状態で圏外から復帰した直後などに起きる一時的な失敗で、セッション自体は
 *   端末に残っている。null にすると「ログインの有効期限が切れました」と表示してログアウトさせてしまう。
 */
export async function readAccessToken(auth: SessionReader): Promise<string | null> {
  const { data, error } = await auth.getSession();
  if (error) {
    const status = typeof error.status === "number" ? error.status : 0;
    if (isAuthRetryableFetchError(error) || status === 0 || status >= 500) {
      console.warn("[api] session refresh failed (transient):", error.message);
      throw new ApiError({
        status: 0,
        code: "network_error",
        message: API_ERROR_MESSAGES.network_error,
        cause: error,
      });
    }
    console.warn("[api] failed to read session:", error.message);
    return null;
  }
  return data.session?.access_token ?? null;
}

let defaultClient: ApiClient | undefined;

export function getApiClient(): ApiClient {
  defaultClient ??= createApiClient({
    baseUrl: getPublicEnv().apiBaseUrl,
    getAccessToken: () => readAccessToken(getSupabaseBrowserClient().auth),
  });
  return defaultClient;
}

/** 既定クライアント（各メソッドは ApiClient と同じシグネチャ） */
export const api: ApiClient = {
  health: (...args) => getApiClient().health(...args),
  createConversation: (...args) => getApiClient().createConversation(...args),
  sendChat: (...args) => getApiClient().sendChat(...args),
  listMemories: (...args) => getApiClient().listMemories(...args),
  createMemory: (...args) => getApiClient().createMemory(...args),
  updateMemory: (...args) => getApiClient().updateMemory(...args),
  deleteMemory: (...args) => getApiClient().deleteMemory(...args),
  createComment: (...args) => getApiClient().createComment(...args),
};

export { ApiError, isApiError, getErrorMessage, toAppError, API_ERROR_MESSAGES } from "./errors";
export type { ApiErrorKind } from "./errors";
