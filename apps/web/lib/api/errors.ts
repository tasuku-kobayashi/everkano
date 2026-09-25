import type { ApiErrorBody, ApiErrorCode } from "@everkano/shared";

/** クライアント側で発生するエラーコード（HTTP 応答が得られなかった場合など） */
export type ClientErrorCode = "network_error" | "timeout" | "aborted" | "unknown";

export type ApiErrorKind = ApiErrorCode | ClientErrorCode;

/** ユーザーに表示する日本語メッセージ（サーバーが message を返さなかった場合の既定値） */
export const API_ERROR_MESSAGES = {
  network_error: "通信できませんでした。電波の良い場所で再度お試しください",
  timeout: "応答に時間がかかっています。電波の良い場所で再度お試しください",
  aborted: "通信がキャンセルされました",
  unknown: "エラーが発生しました。しばらくしてから再度お試しください",
  unauthorized: "ログインの有効期限が切れました。もう一度ログインしてください",
  forbidden: "この操作は許可されていません",
  account_deleted: "このアカウントは退会済みです",
  not_found: "見つかりませんでした",
  validation_error: "入力内容を確認してください",
  moderation_blocked: "この内容は送信できません。表現を変えてもう一度お試しください",
  rate_limited: "少し時間をおいてから再度お試しください",
  llm_unavailable: "ただいま混み合っています。少し時間をおいてから再度お試しください",
  internal_error: "サーバーでエラーが発生しました。しばらくしてから再度お試しください",
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

/** 任意の例外を画面表示用の日本語メッセージにする */
export function getErrorMessage(
  error: unknown,
  fallback: string = API_ERROR_MESSAGES.unknown,
): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return API_ERROR_MESSAGES.network_error;
  return fallback;
}
