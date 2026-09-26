/**
 * Python API の応答の検証と正規化。
 *
 * 型（packages/shared/src/api.ts）が単一の正だが、エンジン v1.0 の項目（is_proactive・kind・safety など）を
 * まだ返さない API（段階的なデプロイの途中・古い版）でも画面が壊れないよう、欠けている項目は既定値で補う。
 * 必須の項目（id・本文など）が欠けていれば null を返し、呼び出し側は internal_error として扱う。
 */

import {
  MEMORY_TAG_SUMMARY,
  type ChatResponse,
  type DuePrecision,
  type MemoryDTO,
  type MemoryKind,
  type MessageDTO,
  type PromiseDTO,
  type PromiseStatus,
  type ProactiveSettingsResponse,
  type SafetyInfo,
  type SafetyResource,
  type SafetyResourcesResponse,
} from "@everkano/shared";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** MessageDTO（is_proactive・safety_triggered が無ければ false）。形が合わなければ null */
export function normalizeMessageDTO(value: unknown): MessageDTO | null {
  if (!isRecord(value)) return null;
  const { id, conversation_id, sender_type, body, created_at, is_proactive, safety_triggered } =
    value;
  if (
    typeof id !== "string" ||
    typeof conversation_id !== "string" ||
    typeof body !== "string" ||
    typeof created_at !== "string" ||
    (sender_type !== "user" && sender_type !== "character")
  ) {
    return null;
  }
  return {
    id,
    conversation_id,
    sender_type,
    body,
    created_at,
    is_proactive: is_proactive === true,
    safety_triggered: safety_triggered === true && sender_type === "character",
  };
}

/** 相談窓口の一覧（名前の無い窓口は捨てる。空の文字列は null） */
export function normalizeSafetyResources(value: unknown): SafetyResource[] {
  return (Array.isArray(value) ? value : []).filter(isRecord).flatMap((r) => {
    const name = optionalString(r.name);
    return name
      ? [
          {
            name,
            phone: optionalString(r.phone),
            hours: optionalString(r.hours),
            url: optionalString(r.url),
          },
        ]
      : [];
  });
}

/** E6 の安全対応の情報。triggered でなければ null（名前の無い窓口は捨てる） */
export function normalizeSafetyInfo(value: unknown): SafetyInfo | null {
  if (!isRecord(value) || value.triggered !== true) return null;
  return { triggered: true, resources: normalizeSafetyResources(value.resources) };
}

/** GET /safety/resources の応答。形が合わなければ null */
export function normalizeSafetyResourcesResponse(value: unknown): SafetyResourcesResponse | null {
  if (!isRecord(value) || !Array.isArray(value.resources)) return null;
  return { resources: normalizeSafetyResources(value.resources) };
}

/**
 * ChatResponse（/chat の応答・/chat/stream の done）。形が合わなければ null。
 * 省略され得る項目（古い API の safety など）は既定値で補う。
 */
export function normalizeChatResponse(value: unknown): ChatResponse | null {
  if (!isRecord(value)) return null;
  const userMessage = normalizeMessageDTO(value.user_message);
  const characterMessage = normalizeMessageDTO(value.character_message);
  if (!userMessage || !characterMessage) return null;
  return {
    message_id: typeof value.message_id === "string" ? value.message_id : characterMessage.id,
    reply: typeof value.reply === "string" ? value.reply : characterMessage.body,
    memories_used: stringArray(value.memories_used),
    memories_created: stringArray(value.memories_created),
    user_message: userMessage,
    character_message: characterMessage,
    moderated: value.moderated === true,
    safety: normalizeSafetyInfo(value.safety),
  };
}

export const MEMORY_KIND_VALUES: readonly MemoryKind[] = [
  "fact",
  "preference",
  "episode",
  "promise",
  "emotion",
  "relationship",
  "summary",
] as const;

function isMemoryKind(value: unknown): value is MemoryKind {
  return typeof value === "string" && (MEMORY_KIND_VALUES as readonly string[]).includes(value);
}

/**
 * MemoryDTO。kind が無ければタグから推定（要約タグなら summary、それ以外は fact）、status は active。
 * 形が合わなければ null。
 */
export function normalizeMemoryDTO(value: unknown): MemoryDTO | null {
  if (!isRecord(value)) return null;
  const { id, character_id, content, created_at } = value;
  if (
    typeof id !== "string" ||
    typeof character_id !== "string" ||
    typeof content !== "string" ||
    typeof created_at !== "string"
  ) {
    return null;
  }
  const tags = stringArray(value.tags);
  const status = value.status === "superseded" ? "superseded" : "active";
  return {
    id,
    character_id,
    content,
    importance: typeof value.importance === "number" ? value.importance : 0.5,
    tags,
    is_user_edited: value.is_user_edited === true,
    source_message_id: optionalString(value.source_message_id),
    created_at,
    updated_at: typeof value.updated_at === "string" ? value.updated_at : created_at,
    kind: isMemoryKind(value.kind)
      ? value.kind
      : tags.includes(MEMORY_TAG_SUMMARY)
        ? "summary"
        : "fact",
    status,
    superseded_by: optionalString(value.superseded_by),
    superseded_at:
      optionalString(value.superseded_at) ??
      (status === "superseded" ? (optionalString(value.updated_at) ?? created_at) : null),
    last_referenced_at: optionalString(value.last_referenced_at),
    reference_count: typeof value.reference_count === "number" ? value.reference_count : 0,
  };
}

const PROMISE_STATUSES: readonly PromiseStatus[] = ["pending", "mentioned", "done", "cancelled"];
const DUE_PRECISIONS: readonly DuePrecision[] = ["datetime", "day", "week", "month", "unknown"];

/** PromiseDTO。形が合わなければ null */
export function normalizePromiseDTO(value: unknown): PromiseDTO | null {
  if (!isRecord(value)) return null;
  const { id, character_id, content, created_at } = value;
  if (
    typeof id !== "string" ||
    typeof character_id !== "string" ||
    typeof content !== "string" ||
    typeof created_at !== "string"
  ) {
    return null;
  }
  const dueAt = optionalString(value.due_at);
  return {
    id,
    character_id,
    content,
    due_at: dueAt,
    due_precision:
      dueAt === null
        ? "unknown"
        : (DUE_PRECISIONS as readonly unknown[]).includes(value.due_precision)
          ? (value.due_precision as DuePrecision)
          : "day",
    status: (PROMISE_STATUSES as readonly unknown[]).includes(value.status)
      ? (value.status as PromiseStatus)
      : "pending",
    created_at,
    updated_at: typeof value.updated_at === "string" ? value.updated_at : created_at,
  };
}

function isHour(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 23;
}

/** GET /proactive/settings の応答か（PUT の応答が設定全体でない API にも対応するため） */
export function isProactiveSettingsResponse(value: unknown): value is ProactiveSettingsResponse {
  if (!isRecord(value) || !isRecord(value.global) || !Array.isArray(value.characters)) return false;
  const { enabled, quiet_start, quiet_end } = value.global;
  return (
    typeof enabled === "boolean" &&
    isHour(quiet_start) &&
    isHour(quiet_end) &&
    value.characters.every(
      (c) => isRecord(c) && typeof c.character_id === "string" && typeof c.enabled === "boolean",
    )
  );
}
