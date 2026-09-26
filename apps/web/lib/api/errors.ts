import type { ApiErrorBody, ApiErrorCode } from "@everkano/shared";

/** クライアント側で発生するエラーコード（HTTP 応答が得られなかった場合など） */
export type ClientErrorCode = "network_error" | "timeout" | "aborted" | "unknown";

export type ApiErrorKind = ApiErrorCode | ClientErrorCode;

/**
 * ログインの有効期限切れ（API の 401 unauthorized とログイン画面の ?error=session で共通の文言）。
 * lib/auth/redirect.ts の LOGIN_ERROR_MESSAGES.session もこれを使う（同じ文が 2 通りの表記にならないように）。
 */
export const SESSION_EXPIRED_MESSAGE =
  "ログインの有効期限が切れました。もう一度ログインしてください。";

/**
 * ユーザーに表示する日本語メッセージ（サーバーが message を返さなかった場合・通信失敗時の既定値）。
 *
 * 文言の決まり（Python API の DEFAULT_MESSAGES（apps/api/app/core/errors.py）と揃える）:
 * - エラー・案内の文は句点「。」で終える（サーバーの message とそのまま並ぶため）
 * - 時間をおいた再試行の案内は「しばらくしてから再度お試しください。」に統一する
 * - 通信失敗（fetch の失敗）は端末の電波だけでなく、サーバーの停止・再起動でも起きる。
 *   原因を端末側（電波）と決めつけない文言にする
 */
export const API_ERROR_MESSAGES = {
  network_error: "通信できませんでした。接続を確認して、しばらくしてから再度お試しください。",
  timeout: "応答に時間がかかっています。しばらくしてから再度お試しください。",
  aborted: "通信がキャンセルされました。",
  unknown: "エラーが発生しました。しばらくしてから再度お試しください。",
  unauthorized: SESSION_EXPIRED_MESSAGE,
  forbidden: "この操作は許可されていません。",
  account_deleted: "このアカウントは退会済みです。",
  not_found: "見つかりませんでした。",
  validation_error: "入力内容を確認してください。",
  moderation_blocked: "この内容は送信できません。表現を変えて再度お試しください。",
  rate_limited: "操作が集中しています。しばらくしてから再度お試しください。",
  llm_unavailable: "ただいま混み合っています。しばらくしてから再度お試しください。",
  internal_error: "サーバーでエラーが発生しました。しばらくしてから再度お試しください。",
} as const satisfies Record<ApiErrorKind, string>;

export interface ApiErrorInit {
  status: number;
  code: ApiErrorKind;
  message: string;
  requestId?: string | undefined;
  retryAfterSeconds?: number | undefined;
  cause?: unknown;
}

/**
 * Python API 呼び出しの失敗を表す例外。
 * - status: HTTP ステータス（通信失敗・タイムアウト・中断は 0）
 * - code: API の error.code（ApiErrorCode）またはクライアント側コード
 * - message: そのまま画面に出せる日本語メッセージ
 * - retryAfterSeconds: 429 の Retry-After（秒）
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorKind;
  readonly requestId: string | undefined;
  readonly retryAfterSeconds: number | undefined;

  constructor(init: ApiErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId;
    this.retryAfterSeconds = init.retryAfterSeconds;
  }

  /** 再試行しても結果が変わらないクライアントエラー（4xx。429 を除く） */
  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500 && this.status !== 429;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** HTTP ステータスからエラーコードを推定する（レスポンス本文が ApiErrorBody でない場合） */
export function codeFromStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 422 || status === 400) return "validation_error";
  if (status === 429) return "rate_limited";
  if (status === 503 || status === 502 || status === 504) return "llm_unavailable";
  if (status >= 500) return "internal_error";
  return "unknown";
}

const KNOWN_CODES = new Set<string>(Object.keys(API_ERROR_MESSAGES));

export function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

/** Retry-After ヘッダ（秒 or HTTP-date）を秒に変換 */
export function parseRetryAfter(
  value: string | null,
  now: number = Date.now(),
): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number.parseInt(trimmed, 10);
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return undefined;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

/** HTTP エラーレスポンスから ApiError を作る */
export function apiErrorFromResponse(
  status: number,
  body: unknown,
  headers: { get(name: string): string | null },
  fallbackRequestId?: string,
): ApiError {
  const retryAfterSeconds = parseRetryAfter(headers.get("Retry-After"));
  const headerRequestId = headers.get("X-Request-ID") ?? fallbackRequestId;

  if (isApiErrorBody(body)) {
    const code: ApiErrorKind = KNOWN_CODES.has(body.error.code)
      ? body.error.code
      : codeFromStatus(status);
    const message = body.error.message.trim() || API_ERROR_MESSAGES[code];
    return new ApiError({
      status,
      code,
      message,
      requestId: body.error.request_id ?? headerRequestId,
      retryAfterSeconds,
    });
  }

  const code = codeFromStatus(status);
  return new ApiError({
    status,
    code,
    message: API_ERROR_MESSAGES[code],
    requestId: headerRequestId,
    retryAfterSeconds,
  });
}

/** エラーコード → HTTP ステータス（/chat/stream の `error` イベントはステータスを持たないため） */
const STATUS_BY_CODE: Partial<Record<ApiErrorKind, number>> = {
  unauthorized: 401,
  forbidden: 403,
  account_deleted: 403,
  not_found: 404,
  validation_error: 422,
  moderation_blocked: 422,
  rate_limited: 429,
  llm_unavailable: 503,
  internal_error: 500,
};

/**
 * ストリームの途中で届いた `error` イベント（ApiErrorBody["error"]）を ApiError にする。
 * HTTP のエラー応答と同じように、コードに対応するステータスを付ける（再試行・ログアウトの判定が同じになる）。
 */
export function apiErrorFromStreamError(
  body: { code: string; message: string; request_id?: string },
  fallbackRequestId?: string,
): ApiError {
  const code: ApiErrorKind = KNOWN_CODES.has(body.code)
    ? (body.code as ApiErrorKind)
    : "internal_error";
  return new ApiError({
    status: STATUS_BY_CODE[code] ?? 500,
    code,
    message: body.message.trim() || API_ERROR_MESSAGES[code],
    requestId: body.request_id ?? fallbackRequestId,
  });
}

// ---------------------------------------------------------------------------
// Supabase（PostgREST / Auth）のエラーを ApiError にそろえる
// ---------------------------------------------------------------------------

/** 接続・一時的な資源不足（再試行してよい）。lib/query-retry.ts と同じ判定 */
const TRANSIENT_POSTGREST_CODE = /^(?:PGRST00\d|08|40|53|57P)/;

interface ErrorLike {
  name?: unknown;
  code?: unknown;
  status?: unknown;
  message?: unknown;
}

function asErrorLike(error: unknown): ErrorLike | null {
  return typeof error === "object" && error !== null ? (error as ErrorLike) : null;
}

/**
 * supabase-js（postgrest-js）が返すエラー（`{ code, message, details, hint }` のプレーンなオブジェクト）か。
 * postgrest-js は throwOnError() を使わない限り Error のインスタンスを作らない。通信失敗は code が ""。
 */
export function isPostgrestErrorLike(error: unknown): boolean {
  const e = asErrorLike(error);
  return (
    e !== null &&
    !(error instanceof Error) &&
    typeof e.code === "string" &&
    typeof e.message === "string"
  );
}

/** 通信層の失敗（fetch の失敗・中断・タイムアウト）のメッセージから ApiError のコードを決める */
function transportErrorCode(nameOrMessage: string): "timeout" | "aborted" | "network_error" {
  if (/^TimeoutError\b/.test(nameOrMessage)) return "timeout";
  if (/^AbortError\b/.test(nameOrMessage)) return "aborted";
  return "network_error";
}

function fromCode(code: ApiErrorKind, status: number, cause: unknown): ApiError {
  return new ApiError({ status, code, message: API_ERROR_MESSAGES[code], cause });
}

/**
 * 任意の例外を ApiError（日本語 message・status・code 付き）に変換する。元のエラーは cause に残す。
 *
 * - ApiError はそのまま返す
 * - PostgREST（supabase-js の `{ error }`）:
 *   - 通信失敗（code ""）→ status 0 の network_error（中断は aborted、タイムアウトは timeout）
 *   - 42501（権限不足）・PGRST301〜303（JWT の不正）→ 403 forbidden（ここではログアウトさせない）
 *   - PGRST116（0 件）→ 404 not_found
 *   - 接続・一時的な資源不足（PGRST000〜009・08*・40*・53*・57P*）→ 503 internal_error（再試行してよい）
 *   - その他の SQLSTATE / PGRST コード → 400 unknown（再試行しても変わらない）
 * - supabase-js Auth のエラー: 通信失敗（AuthRetryableFetchError）→ network_error、5xx → internal_error、
 *   それ以外 → その status の unknown（unauthorized には変換しない。グローバルなログアウトを誘発しないため）
 * - TypeError（fetch の失敗）→ network_error、DOMException の AbortError / TimeoutError → aborted / timeout
 *
 * 画面のエラー表示（getErrorMessage）と React Query の再試行判定（lib/query-retry.ts）が、
 * Python API と Supabase のどちらの失敗でも同じように振る舞うようにするために使う。
 * lib/queries の fetcher は `if (error) throw toAppError(error);` と書く。
 */
export function toAppError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  const e = asErrorLike(error);
  const name = typeof e?.name === "string" ? e.name : "";
  const message = typeof e?.message === "string" ? e.message : "";

  if (isPostgrestErrorLike(error)) {
    const code = e?.code as string;
    if (code === "") return fromCode(transportErrorCode(message), 0, error);
    if (code === "42501" || /^PGRST30[1-3]$/.test(code)) return fromCode("forbidden", 403, error);
    if (code === "PGRST116") return fromCode("not_found", 404, error);
    if (TRANSIENT_POSTGREST_CODE.test(code)) return fromCode("internal_error", 503, error);
    return fromCode("unknown", 400, error);
  }

  if (name.startsWith("Auth")) {
    const status = typeof e?.status === "number" ? e.status : undefined;
    if (name === "AuthRetryableFetchError") return fromCode("network_error", 0, error);
    if (status !== undefined && status >= 500) return fromCode("internal_error", status, error);
    if (status !== undefined && status >= 400) return fromCode("unknown", status, error);
    return fromCode("unknown", 0, error);
  }

  if (name === "TimeoutError" || name === "AbortError") {
    return fromCode(transportErrorCode(name), 0, error);
  }
  if (error instanceof TypeError) return fromCode("network_error", 0, error);
  return fromCode("unknown", 0, error);
}

/**
 * 任意の例外を画面表示用の日本語メッセージにする。
 * ApiError はその message。Supabase のエラー（プレーンなオブジェクト）や fetch の失敗は toAppError で分類し、
 * 通信失敗なら通信エラーの案内を出す。分類できないものは fallback。
 */
export function getErrorMessage(
  error: unknown,
  fallback: string = API_ERROR_MESSAGES.unknown,
): string {
  if (error instanceof ApiError) return error.message;
  const converted = toAppError(error);
  return converted.code === "unknown" ? fallback : converted.message;
}
