import { isAuthRetryableFetchError } from "@supabase/supabase-js";
import type {
  ChatRequest,
  ChatResponse,
  ChatStreamEvent,
  CreateCommentRequest,
  CreateCommentResponse,
  CreateConversationRequest,
  CreateConversationResponse,
  CreateMemoryRequest,
  HealthResponse,
  ListMemoriesResponse,
  ListPromisesResponse,
  MemoryDTO,
  PromiseDTO,
  ProactiveSettingsResponse,
  SafetyResourcesResponse,
  UpdateMemoryRequest,
  UpdateProactiveCharacterSettingRequest,
  UpdateProactiveGlobalSettingsRequest,
  UpdatePromiseRequest,
  UUID,
} from "@everkano/shared";
import { getPublicEnv } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import {
  API_ERROR_MESSAGES,
  ApiError,
  apiErrorFromResponse,
  apiErrorFromStreamError,
} from "./errors";
import {
  isProactiveSettingsResponse,
  normalizeChatResponse,
  normalizeMemoryDTO,
  normalizePromiseDTO,
  normalizeSafetyResourcesResponse,
} from "./normalize";
import { createSseParser, toChatStreamEvent, type SseMessage } from "./sse";

/**
 * Python API（apps/api / FastAPI）の型付きクライアント。
 *
 * 型は packages/shared/src/api.ts が単一の正。すべての認証付きエンドポイントに
 * Supabase のアクセストークンを `Authorization: Bearer` で付与する。
 * 失敗時は必ず ApiError（日本語 message 付き）を投げる。
 *
 * 使い方（クライアントコンポーネント）:
 *   import { api } from "@/lib/api/client";   // または "@/lib/api"
 *   const res = await api.streamChat({ character_id, conversation_id, message }, { onEvent });
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
/**
 * POST /chat/stream の締め切り（接続から `done` まで）。/chat と同じ 45 秒（API の締め切り 38 秒 + 保存）。
 * 返答の途中で打ち切ると、サーバーでは保存済みの発言を失敗表示にしてしまうため、区切りごとの無通信
 * タイムアウトは設けない（API 側の締め切りが先に来て `error` が届く）。
 */
export const CHAT_STREAM_TIMEOUT_MS = CHAT_TIMEOUT_MS;

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
  chatStreamTimeoutMs?: number;
  /**
   * ストリーミング（ReadableStream + TextDecoder）が使えるか。既定は実行環境から判定する。
   * false なら /chat/stream を使わず /chat（返答の全文を一度に受け取る）にする。
   */
  supportsStreaming?: () => boolean;
  /** X-Request-ID を生成する（既定: crypto.randomUUID） */
  generateRequestId?: () => string;
}

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** /chat/stream の送信オプション */
export interface StreamChatOptions extends RequestOptions {
  /**
   * イベントを受け取るたびに呼ばれる（delta / replace / done。error は例外として投げる前に呼ぶ）。
   * ストリーミングを使えない場合（/chat にフォールバック）は done だけが 1 回呼ばれる。
   */
  onEvent?: (event: ChatStreamEvent) => void;
}

/** GET /memories のオプション */
export interface ListMemoriesOptions extends RequestOptions {
  /** 置き換えられた古い記憶（履歴。status = superseded）も含める */
  includeSuperseded?: boolean;
}

/** GET /promises のオプション */
export interface ListPromisesOptions extends RequestOptions {
  /** 完了・取り消し済みも含める */
  includeClosed?: boolean;
}

/**
 * /chat/stream が API に無い（404 / 405）ことを表す内部の目印。/chat にフォールバックする。
 * （所有者チェックの 404 と区別できないため、/chat も失敗したらその失敗をそのまま返す）
 */
class StreamEndpointUnavailable extends Error {
  constructor(readonly apiError: ApiError) {
    super(apiError.message);
    this.name = "StreamEndpointUnavailable";
  }
}

/** fetch の本文を ReadableStream で読み、TextDecoder で逐次デコードできる環境か */
export function defaultSupportsStreaming(): boolean {
  return (
    typeof ReadableStream !== "undefined" &&
    typeof TextDecoder !== "undefined" &&
    typeof Response !== "undefined" &&
    "body" in Response.prototype
  );
}

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
  const chatStreamTimeoutMs = config.chatStreamTimeoutMs ?? CHAT_STREAM_TIMEOUT_MS;
  const generateRequestId = config.generateRequestId ?? defaultRequestId;
  const supportsStreaming = config.supportsStreaming ?? defaultSupportsStreaming;
  /** /chat/stream が無い API だと分かった（以降は /chat を直接使う） */
  let streamUnavailable = false;

  /** 認証ヘッダー・X-Request-ID・URL を組み立てる（未ログインなら 401 unauthorized） */
  async function prepare(options: {
    path: string;
    body?: unknown;
    query?: Record<string, string>;
    auth: boolean;
    accept: string;
  }): Promise<{ url: string; headers: Record<string, string> }> {
    const headers: Record<string, string> = {
      Accept: options.accept,
      "X-Request-ID": generateRequestId(),
    };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";

    if (options.auth) {
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

    const url = new URL(`${baseUrl}${options.path}`);
    if (options.query) {
      for (const [key, value] of Object.entries(options.query)) url.searchParams.set(key, value);
    }
    return { url: url.toString(), headers };
  }

  /** 通信層の失敗（fetch・本文の読み取り）を ApiError にする */
  function transportError(
    cause: unknown,
    state: { timedOut: boolean; signal: AbortSignal | undefined; requestId: string },
  ): ApiError {
    if (state.timedOut) {
      return new ApiError({
        status: 0,
        code: "timeout",
        message: API_ERROR_MESSAGES.timeout,
        requestId: state.requestId,
        cause,
      });
    }
    if (state.signal?.aborted) {
      return new ApiError({
        status: 0,
        code: "aborted",
        message: API_ERROR_MESSAGES.aborted,
        cause,
      });
    }
    return new ApiError({
      status: 0,
      code: "network_error",
      message: API_ERROR_MESSAGES.network_error,
      requestId: state.requestId,
      cause,
    });
  }

  async function call<T>(options: CallOptions): Promise<T> {
    const { method, path, body, query, auth = true, signal } = options;
    const timeoutMs = options.timeoutMs ?? options.defaultTimeoutMs;

    if (signal?.aborted) {
      throw new ApiError({ status: 0, code: "aborted", message: API_ERROR_MESSAGES.aborted });
    }

    const { url, headers } = await prepare({
      path,
      body,
      query,
      auth,
      accept: "application/json",
    });
    // トークンの取得中に中断された（abort イベントはもう来ない）
    if (signal?.aborted) {
      throw new ApiError({ status: 0, code: "aborted", message: API_ERROR_MESSAGES.aborted });
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
      response = await doFetch(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      });
    } catch (cause) {
      throw transportError(cause, { timedOut, signal, requestId: headers["X-Request-ID"]! });
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

  /** 応答の本文を JSON として読む（読めなければ undefined） */
  async function readJson(response: Response): Promise<unknown> {
    const text = await response.text().catch(() => "");
    if (!text) return undefined;
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return undefined;
    }
  }

  function malformedResponse(requestId: string): ApiError {
    return new ApiError({
      status: 200,
      code: "internal_error",
      message: API_ERROR_MESSAGES.internal_error,
      requestId,
    });
  }

  /** /chat の応答を正規化する（is_proactive・safety の無い古い API にも合わせる） */
  async function sendChatOnce(body: ChatRequest, options: RequestOptions): Promise<ChatResponse> {
    const raw = await call<unknown>({
      ...options,
      method: "POST",
      path: "/chat",
      body,
      defaultTimeoutMs: chatTimeoutMs,
    });
    const response = normalizeChatResponse(raw);
    if (!response) throw malformedResponse("");
    return response;
  }

  /**
   * SSE の本文を読み、イベントごとに onEvent を呼ぶ。`done` で結果を返す（残りは読まずに閉じる）。
   * `error` は ApiError として投げる。`done` の前に接続が閉じたら network_error。
   */
  async function readChatStream(
    response: Response,
    onEvent: StreamChatOptions["onEvent"],
    requestId: string,
  ): Promise<ChatResponse> {
    const parser = createSseParser();
    let result: ChatResponse | null = null;
    /** イベントを処理し、done が来たら true */
    const handle = (messages: readonly SseMessage[]): boolean => {
      for (const message of messages) {
        const event = toChatStreamEvent(message);
        if (!event) continue;
        onEvent?.(event);
        if (event.type === "error") throw apiErrorFromStreamError(event.data, requestId);
        if (event.type === "done") {
          result = event.data;
          return true;
        }
      }
      return false;
    };

    if (!response.body) {
      // 本文をストリームで読めない環境: 全文を受け取ってからまとめて処理する
      const text = await response.text();
      if (handle(parser.push(text)) || handle(parser.end())) return result!;
    } else {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          if (handle(parser.push(decoder.decode(value, { stream: true })))) return result!;
        }
        if (handle(parser.push(decoder.decode())) || handle(parser.end())) return result!;
      } finally {
        // done / error の後は残りを読まずに接続を閉じる（読み終えていれば何もしない）
        reader.cancel().catch(() => undefined);
      }
    }
    // done も error も無いまま閉じた（中継の切断など）。サーバーでは保存済みの可能性があるが、
    // その場合は Realtime / 差分取得で届いた保存済みの発言が送信失敗の吹き出しを置き換える
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: API_ERROR_MESSAGES.network_error,
      requestId,
    });
  }

  /** POST /chat/stream を 1 回実行する（API に無ければ StreamEndpointUnavailable を投げる） */
  async function streamChatOnce(
    body: ChatRequest,
    options: StreamChatOptions,
  ): Promise<ChatResponse> {
    const { signal, onEvent } = options;
    const timeoutMs = options.timeoutMs ?? chatStreamTimeoutMs;
    if (signal?.aborted) {
      throw new ApiError({ status: 0, code: "aborted", message: API_ERROR_MESSAGES.aborted });
    }
    const { url, headers } = await prepare({
      path: "/chat/stream",
      body,
      auth: true,
      accept: "text/event-stream",
    });
    const requestId = headers["X-Request-ID"]!;
    if (signal?.aborted) {
      throw new ApiError({ status: 0, code: "aborted", message: API_ERROR_MESSAGES.aborted });
    }

    // タイムアウトは接続から done まで（本文の読み取り中も有効）
    const controller = new AbortController();
    const state = { timedOut: false, signal, requestId };
    const timer = setTimeout(() => {
      state.timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onExternalAbort = () => controller.abort();
    signal?.addEventListener("abort", onExternalAbort, { once: true });

    try {
      let response: Response;
      try {
        response = await doFetch(url, {
          method: "POST",
          headers,
          body: JSON.stringify(body),
          signal: controller.signal,
          credentials: "omit",
          cache: "no-store",
        });
      } catch (cause) {
        throw transportError(cause, state);
      }

      // 認証・所有者・レート制限などはストリーム開始前に通常の JSON エラーで返る
      if (!response.ok) {
        const parsed = await readJson(response);
        const error = apiErrorFromResponse(response.status, parsed, response.headers, requestId);
        console.warn(
          `[api] POST /chat/stream -> ${response.status} ${error.code} (request_id=${error.requestId ?? "-"})`,
        );
        if (response.status === 404 || response.status === 405) {
          throw new StreamEndpointUnavailable(error);
        }
        throw error;
      }

      try {
        const contentType = (response.headers.get("Content-Type") ?? "").toLowerCase();
        if (!contentType.includes("text/event-stream")) {
          // ストリームにせず ChatResponse を JSON で返す中継・API にも対応する
          const result = normalizeChatResponse(await readJson(response));
          if (!result) throw malformedResponse(requestId);
          onEvent?.({ type: "done", data: result });
          return result;
        }
        return await readChatStream(response, onEvent, requestId);
      } catch (cause) {
        if (cause instanceof ApiError) throw cause;
        throw transportError(cause, state);
      }
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onExternalAbort);
    }
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

    /** POST /chat — DM 送信 → キャラの返答（全文を一度に受け取る。タイムアウト 45 秒） */
    sendChat(body: ChatRequest, options: RequestOptions = {}): Promise<ChatResponse> {
      return sendChatOnce(body, options);
    },

    /**
     * POST /chat/stream — DM 送信 → キャラの返答を順に受け取る（E8: 最初の文字を早く表示する）。
     * - delta / replace は onEvent で届き、結果（保存済みの 2 件のメッセージ）は done の ChatResponse で返る
     * - ストリーム開始前の HTTP エラー（401・404・429 など）と `error` イベントは ApiError を投げる
     * - ストリーミングを使えない環境・/chat/stream が無い API では /chat にフォールバックし、done だけを呼ぶ
     * - タイムアウトは接続から done まで 45 秒（CHAT_STREAM_TIMEOUT_MS）
     */
    async streamChat(body: ChatRequest, options: StreamChatOptions = {}): Promise<ChatResponse> {
      const fallback = async (): Promise<ChatResponse> => {
        const result = await sendChatOnce(body, {
          signal: options.signal,
          timeoutMs: options.timeoutMs,
        });
        options.onEvent?.({ type: "done", data: result });
        return result;
      };
      if (streamUnavailable || !supportsStreaming()) return fallback();
      try {
        return await streamChatOnce(body, options);
      } catch (error) {
        if (!(error instanceof StreamEndpointUnavailable)) throw error;
        // /chat/stream が無い（古い API）か、会話が見つからない（所有者チェック）。/chat で確かめる
        const result = await fallback();
        streamUnavailable = true;
        return result;
      }
    },

    /** GET /memories?character_id=&include_superseded= — そのキャラとの記憶一覧 */
    async listMemories(
      characterId: UUID,
      options: ListMemoriesOptions = {},
    ): Promise<ListMemoriesResponse> {
      const { includeSuperseded, ...rest } = options;
      const query: Record<string, string> = { character_id: characterId };
      if (includeSuperseded) query.include_superseded = "true";
      const raw = await call<{ memories?: unknown }>({
        ...rest,
        method: "GET",
        path: "/memories",
        query,
        defaultTimeoutMs,
      });
      const list = Array.isArray(raw.memories) ? raw.memories : [];
      return {
        memories: list.flatMap((item) => {
          const memory = normalizeMemoryDTO(item);
          return memory ? [memory] : [];
        }),
      };
    },

    /** POST /memories — 記憶を追加（is_user_edited = true） */
    async createMemory(
      body: CreateMemoryRequest,
      options: RequestOptions = {},
    ): Promise<MemoryDTO> {
      const raw = await call<unknown>({
        ...options,
        method: "POST",
        path: "/memories",
        body,
        defaultTimeoutMs,
      });
      const memory = normalizeMemoryDTO(raw);
      if (!memory) throw malformedResponse("");
      return memory;
    },

    /** PATCH /memories/{id} — 内容・重要度・タグ・種類を更新 */
    async updateMemory(
      memoryId: UUID,
      body: UpdateMemoryRequest,
      options: RequestOptions = {},
    ): Promise<MemoryDTO> {
      const raw = await call<unknown>({
        ...options,
        method: "PATCH",
        path: `/memories/${encodeURIComponent(memoryId)}`,
        body,
        defaultTimeoutMs,
      });
      const memory = normalizeMemoryDTO(raw);
      if (!memory) throw malformedResponse("");
      return memory;
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

    /** GET /promises?character_id=&include_closed= — そのキャラとの約束（既定は未達・話題にしたものだけ） */
    async listPromises(
      characterId: UUID,
      options: ListPromisesOptions = {},
    ): Promise<ListPromisesResponse> {
      const { includeClosed, ...rest } = options;
      const query: Record<string, string> = { character_id: characterId };
      if (includeClosed) query.include_closed = "true";
      const raw = await call<{ promises?: unknown }>({
        ...rest,
        method: "GET",
        path: "/promises",
        query,
        defaultTimeoutMs,
      });
      const list = Array.isArray(raw.promises) ? raw.promises : [];
      return {
        promises: list.flatMap((item) => {
          const promise = normalizePromiseDTO(item);
          return promise ? [promise] : [];
        }),
      };
    },

    /** PATCH /promises/{id} — 約束を完了・取り消しにする */
    async updatePromise(
      promiseId: UUID,
      body: UpdatePromiseRequest,
      options: RequestOptions = {},
    ): Promise<PromiseDTO | null> {
      const raw = await call<unknown>({
        ...options,
        method: "PATCH",
        path: `/promises/${encodeURIComponent(promiseId)}`,
        body,
        defaultTimeoutMs,
      });
      return normalizePromiseDTO(raw);
    },

    /** GET /proactive/settings — 自発メッセージの設定（全体 + キャラ別） */
    async getProactiveSettings(options: RequestOptions = {}): Promise<ProactiveSettingsResponse> {
      const raw = await call<unknown>({
        ...options,
        method: "GET",
        path: "/proactive/settings",
        defaultTimeoutMs,
      });
      if (!isProactiveSettingsResponse(raw)) throw malformedResponse("");
      return raw;
    },

    /**
     * PUT /proactive/settings — 全体の設定を更新（省略した項目は変更しない）。
     * 応答が設定全体でなければ null（呼び出し側は取り直す）。
     */
    async updateProactiveSettings(
      body: UpdateProactiveGlobalSettingsRequest,
      options: RequestOptions = {},
    ): Promise<ProactiveSettingsResponse | null> {
      const raw = await call<unknown>({
        ...options,
        method: "PUT",
        path: "/proactive/settings",
        body,
        defaultTimeoutMs,
      });
      return isProactiveSettingsResponse(raw) ? raw : null;
    },

    /** PUT /proactive/settings/{character_id} — キャラ別のオン・オフ（応答が設定全体でなければ null） */
    async updateProactiveCharacterSetting(
      characterId: UUID,
      body: UpdateProactiveCharacterSettingRequest,
      options: RequestOptions = {},
    ): Promise<ProactiveSettingsResponse | null> {
      const raw = await call<unknown>({
        ...options,
        method: "PUT",
        path: `/proactive/settings/${encodeURIComponent(characterId)}`,
        body,
        defaultTimeoutMs,
      });
      return isProactiveSettingsResponse(raw) ? raw : null;
    },

    /** GET /safety/resources — E6 の相談窓口の一覧（安全対応をした返答の下のカード用） */
    async getSafetyResources(options: RequestOptions = {}): Promise<SafetyResourcesResponse> {
      const raw = await call<unknown>({
        ...options,
        method: "GET",
        path: "/safety/resources",
        defaultTimeoutMs,
      });
      const response = normalizeSafetyResourcesResponse(raw);
      if (!response) throw malformedResponse("");
      return response;
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
  streamChat: (...args) => getApiClient().streamChat(...args),
  listMemories: (...args) => getApiClient().listMemories(...args),
  createMemory: (...args) => getApiClient().createMemory(...args),
  updateMemory: (...args) => getApiClient().updateMemory(...args),
  deleteMemory: (...args) => getApiClient().deleteMemory(...args),
  createComment: (...args) => getApiClient().createComment(...args),
  listPromises: (...args) => getApiClient().listPromises(...args),
  updatePromise: (...args) => getApiClient().updatePromise(...args),
  getProactiveSettings: (...args) => getApiClient().getProactiveSettings(...args),
  updateProactiveSettings: (...args) => getApiClient().updateProactiveSettings(...args),
  updateProactiveCharacterSetting: (...args) =>
    getApiClient().updateProactiveCharacterSetting(...args),
  getSafetyResources: (...args) => getApiClient().getSafetyResources(...args),
};

export { ApiError, isApiError, getErrorMessage, toAppError, API_ERROR_MESSAGES } from "./errors";
export type { ApiErrorKind } from "./errors";
