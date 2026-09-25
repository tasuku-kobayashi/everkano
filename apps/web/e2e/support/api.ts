import type {
  ApiErrorBody,
  ChatResponse,
  CreateConversationResponse,
  MemoryDTO,
} from "@everkano/shared";
import { E2E } from "./env";

/**
 * Python API を直接呼ぶヘルパー（RLS / 所有者チェックの検証と、テストの前提データ作成用）。
 * 画面の操作で確認できることは画面で確認する。
 */

export interface ApiResult<T> {
  status: number;
  body: T | ApiErrorBody | null;
}

export async function apiCall<T>(
  token: string | null,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${E2E.apiURL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, body: text ? (JSON.parse(text) as T | ApiErrorBody) : null };
}

export function isApiError(body: unknown): body is ApiErrorBody {
  return typeof body === "object" && body !== null && "error" in body;
}

/** 成功を前提に呼ぶ（失敗したら内容つきで例外） */
async function ok<T>(promise: Promise<ApiResult<T>>, expected = 200): Promise<T> {
  const result = await promise;
  if (result.status !== expected || isApiError(result.body) || result.body === null) {
    throw new Error(`API ${result.status}: ${JSON.stringify(result.body)}`);
  }
  return result.body;
}

export function createConversation(token: string, characterId: string) {
  return ok<CreateConversationResponse>(
    apiCall(token, "POST", "/conversations", { character_id: characterId }),
  );
}

export function chat(token: string, characterId: string, conversationId: string, message: string) {
  return ok<ChatResponse>(
    apiCall(token, "POST", "/chat", {
      character_id: characterId,
      conversation_id: conversationId,
      message,
    }),
  );
}

export function addMemory(token: string, characterId: string, content: string) {
  return ok<MemoryDTO>(
    apiCall(token, "POST", "/memories", { character_id: characterId, content, importance: 0.9 }),
    201,
  );
}
