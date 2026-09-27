/**
 * Typed API client (openapi-fetch over the generated `types.ts`).
 * - Every request carries `X-API-Key` from the store; the same key is mirrored into the `psk` cookie so that
 *   plain <img src="/api/images/..."> requests are authenticated too.
 * - Errors are surfaced as `ApiError` with the server's `detail` message (shown verbatim in toasts).
 */
import createClient from "openapi-fetch";
import { useUiStore } from "../store/ui";
import type { components, paths } from "./types";

export type Schemas = components["schemas"];
export type CharacterSummary = Schemas["CharacterSummary"];
export type CharacterDetail = Schemas["CharacterDetail"];
export type CharacterVersion = Schemas["CharacterVersionSchema"];
export type ImageItem = Schemas["ImageSchema"];
export type ImageDetail = Schemas["ImageDetail"];
export type Job = Schemas["JobSchema"];
export type Health = Schemas["HealthResponse"];
export type Vram = Schemas["VramResponse"];
export type AnalyzeResponse = Schemas["AnalyzeResponse"];
export type AnalyzeItem = Schemas["AnalyzeItem"];
export type ScenePreset = Schemas["ScenePresetSchema"];
export type LockedParams = Schemas["LockedParamsSchema"];
export type LockedPatch = Schemas["LockedParamsPatch"];
export type GenerateRequest = Schemas["GenerateRequest"];
export type PreviewVram = Schemas["PreviewVramResponse"];
export type Quality = Schemas["QualitySchema"];
export type FaceMethod = "pulid" | "faceid" | "instantid";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.name = "ApiError";
  }
}

export const api = createClient<paths>({ baseUrl: "" });

api.use({
  onRequest({ request }) {
    const key = useUiStore.getState().apiKey;
    if (key) request.headers.set("X-API-Key", key);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401) useUiStore.getState().setUnauthorized(true);
    return response;
  },
});

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Convert an openapi-fetch result into data-or-throw. */
export function unwrap<T>(result: Result<T>): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(result.response.status, detailOf(result.error, result.response.status));
  }
  return result.data as T;
}

export function detailOf(error: unknown, status?: number): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          const loc = Array.isArray(d.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
          return loc ? `${loc}: ${d.msg}` : String(d.msg);
        })
        .join(" / ");
    }
  }
  if (status === 401) return "APIキーが無効です。設定画面で確認してください。";
  if (status === 503) return "ComfyUI に接続できません。";
  return status ? `サーバーエラー（${status}）` : "通信に失敗しました。";
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

/** Fetch with the API key for endpoints that return binary data (ZIP download). */
export async function fetchBinary(path: string, init: RequestInit = {}): Promise<Blob> {
  const key = useUiStore.getState().apiKey;
  const headers = new Headers(init.headers);
  if (key) headers.set("X-API-Key", key);
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = detailOf(await res.json(), res.status);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}
