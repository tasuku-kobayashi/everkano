/**
 * Server-Sent Events（text/event-stream）の逐次パーサーと、POST /chat/stream のイベントの検証。
 *
 * - EventSource は GET しか送れず、Authorization ヘッダーも付けられないため、fetch + ReadableStream で読む。
 *   その受信データ（任意の位置で分割されたテキスト）を WHATWG の仕様どおりにイベントへ組み立てる。
 * - 仕様との違いは 1 点だけ: 接続が閉じた時点で空行（イベントの区切り）が来ていないイベントも、data があれば
 *   届いたものとして扱う（end()）。最後の `done` の直後に接続が切れた場合でも結果を受け取れるようにするため。
 */

import type { ApiErrorBody, ChatStreamEvent } from "@everkano/shared";
import { normalizeChatResponse } from "./normalize";

/** 1 件のイベント（event 未指定は "message"） */
export interface SseMessage {
  event: string;
  data: string;
  /** 最後に受け取った id（無ければ ""） */
  id: string;
}

export interface SseParser {
  /** 受信したテキスト（途中で分割されていてよい）を渡し、完成したイベントを返す */
  push(chunk: string): SseMessage[];
  /** 接続が閉じたとき。残りのデータから組み立てられるイベントを返す */
  end(): SseMessage[];
}

export function createSseParser(): SseParser {
  let buffer = "";
  let started = false;
  /** 直前のチャンクが CR で終わった（次のチャンク先頭の LF は同じ改行の一部） */
  let skipLeadingLf = false;
  let eventType = "";
  let dataLines: string[] = [];
  let lastEventId = "";

  const dispatch = (out: SseMessage[]) => {
    if (dataLines.length > 0) {
      out.push({ event: eventType || "message", data: dataLines.join("\n"), id: lastEventId });
    }
    eventType = "";
    dataLines = [];
  };

  const processLine = (line: string, out: SseMessage[]) => {
    if (line === "") {
      dispatch(out);
      return;
    }
    if (line.startsWith(":")) return; // コメント（キープアライブ）
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        eventType = value;
        break;
      case "data":
        dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\0")) lastEventId = value;
        break;
      default:
        // retry など、ここでは使わないフィールドは無視する
        break;
    }
  };

  return {
    push(chunk: string): SseMessage[] {
      let text = chunk;
      if (!started && text.length > 0) {
        started = true;
        if (text.startsWith("﻿")) text = text.slice(1);
      }
      if (skipLeadingLf && text.startsWith("\n")) text = text.slice(1);
      if (text.length > 0) skipLeadingLf = false;
      buffer += text;

      const out: SseMessage[] = [];
      let start = 0;
      for (let i = 0; i < buffer.length; i += 1) {
        const ch = buffer[i];
        if (ch !== "\n" && ch !== "\r") continue;
        const line = buffer.slice(start, i);
        if (ch === "\r") {
          if (i + 1 < buffer.length) {
            if (buffer[i + 1] === "\n") i += 1;
          } else {
            skipLeadingLf = true;
          }
        }
        start = i + 1;
        processLine(line, out);
      }
      buffer = buffer.slice(start);
      return out;
    },

    end(): SseMessage[] {
      const out: SseMessage[] = [];
      if (buffer.length > 0) {
        processLine(buffer, out);
        buffer = "";
      }
      dispatch(out);
      return out;
    },
  };
}

// ---------------------------------------------------------------------------
// POST /chat/stream のイベント
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data) as unknown;
  } catch {
    return undefined;
  }
}

/**
 * SSE のイベント 1 件を ChatStreamEvent に変換する。
 * 知らない種類（将来の拡張・キープアライブ）や形の合わないイベントは null（無視する）。
 */
export function toChatStreamEvent(message: SseMessage): ChatStreamEvent | null {
  const data = parseJson(message.data);
  if (!isRecord(data)) return null;
  switch (message.event) {
    case "delta":
      return typeof data.text === "string" ? { type: "delta", data: { text: data.text } } : null;
    case "replace":
      return typeof data.text === "string"
        ? {
            type: "replace",
            data: { text: data.text, reason: data.reason === "safety" ? "safety" : "moderated" },
          }
        : null;
    case "done": {
      const response = normalizeChatResponse(data);
      return response ? { type: "done", data: response } : null;
    }
    case "error": {
      const error: ApiErrorBody["error"] = {
        code: (typeof data.code === "string"
          ? data.code
          : "internal_error") as ApiErrorBody["error"]["code"],
        message: typeof data.message === "string" ? data.message : "",
      };
      if (typeof data.request_id === "string") error.request_id = data.request_id;
      return { type: "error", data: error };
    }
    default:
      return null;
  }
}
