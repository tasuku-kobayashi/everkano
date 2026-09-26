import { describe, expect, it } from "vitest";
import { createSseParser, toChatStreamEvent, type SseMessage } from "./sse";

/** テキストを指定の位置で分割して順に push し、最後に end する */
function parseChunks(chunks: readonly string[]): SseMessage[] {
  const parser = createSseParser();
  const out: SseMessage[] = [];
  for (const chunk of chunks) out.push(...parser.push(chunk));
  out.push(...parser.end());
  return out;
}

/** 1 文字ずつ分割しても同じ結果になること（どこで分割されても組み立てられる） */
function everySplit(text: string): SseMessage[][] {
  const results: SseMessage[][] = [parseChunks([text]), parseChunks([...text])];
  for (let i = 1; i < text.length; i += 1) {
    results.push(parseChunks([text.slice(0, i), text.slice(i)]));
  }
  return results;
}

describe("createSseParser", () => {
  it("event と data を組み立て、空行で 1 件にする", () => {
    const text = 'event: delta\ndata: {"text":"こんにちは"}\n\nevent: done\ndata: {"a":1}\n\n';
    expect(parseChunks([text])).toEqual([
      { event: "delta", data: '{"text":"こんにちは"}', id: "" },
      { event: "done", data: '{"a":1}', id: "" },
    ]);
  });

  it("どこで分割されて届いても同じイベントになる（マルチバイト文字の途中の分割はデコーダ側の責任）", () => {
    const text = 'event: delta\r\ndata: {"text":"おかえり！"}\r\n\r\nevent: done\rdata: x\r\r';
    const expected = [
      { event: "delta", data: '{"text":"おかえり！"}', id: "" },
      { event: "done", data: "x", id: "" },
    ];
    for (const result of everySplit(text)) expect(result).toEqual(expected);
  });

  it("CRLF がチャンクの境目で CR と LF に分かれても 1 つの改行として扱う", () => {
    expect(parseChunks(["data: a\r", "\ndata: b\r", "\n\r", "\n"])).toEqual([
      { event: "message", data: "a\nb", id: "" },
    ]);
  });

  it("複数行の data は改行でつなぐ・コメントと未知のフィールドは無視・値の先頭の空白は 1 つだけ除く", () => {
    const text = ": keep-alive\nretry: 1000\nfoo: bar\ndata:  x\ndata:y\n\n";
    expect(parseChunks([text])).toEqual([{ event: "message", data: " x\ny", id: "" }]);
  });

  it("コロンの無い行はフィールド名だけ（値は空）", () => {
    expect(parseChunks(["data\ndata\n\n"])).toEqual([{ event: "message", data: "\n", id: "" }]);
  });

  it("data の無いイベントは送らない（event 名もリセットされる）", () => {
    expect(parseChunks(["event: ping\n\ndata: 1\n\n"])).toEqual([
      { event: "message", data: "1", id: "" },
    ]);
  });

  it("先頭の BOM を取り除く・id を引き継ぐ", () => {
    expect(parseChunks(["﻿id: 7\nevent: delta\ndata: 1\n\ndata: 2\n\n"])).toEqual([
      { event: "delta", data: "1", id: "7" },
      { event: "message", data: "2", id: "7" },
    ]);
  });

  it("接続が閉じたときに区切りの空行が来ていないイベントも data があれば返す", () => {
    const parser = createSseParser();
    expect(parser.push('event: done\ndata: {"ok":true}')).toEqual([]);
    expect(parser.end()).toEqual([{ event: "done", data: '{"ok":true}', id: "" }]);
  });
});

describe("toChatStreamEvent", () => {
  const message = (event: string, data: unknown): SseMessage => ({
    event,
    data: typeof data === "string" ? data : JSON.stringify(data),
    id: "",
  });

  const doneData = {
    message_id: "m2",
    reply: "おかえり",
    memories_used: ["x"],
    memories_created: [],
    user_message: {
      id: "m1",
      conversation_id: "c",
      sender_type: "user",
      body: "ただいま",
      created_at: "2026-09-26T00:00:00.000001Z",
      is_proactive: false,
    },
    character_message: {
      id: "m2",
      conversation_id: "c",
      sender_type: "character",
      body: "おかえり",
      created_at: "2026-09-26T00:00:00.000002Z",
    },
    moderated: false,
    safety: {
      triggered: true,
      resources: [
        { name: "よりそいホットライン", phone: "0120-279-338", hours: "24時間", url: null },
        { name: "", phone: "1" },
      ],
    },
  };

  it("delta / replace / done / error を型付きのイベントにする", () => {
    expect(toChatStreamEvent(message("delta", { text: "こん" }))).toEqual({
      type: "delta",
      data: { text: "こん" },
    });
    expect(toChatStreamEvent(message("replace", { text: "ごめんね", reason: "safety" }))).toEqual({
      type: "replace",
      data: { text: "ごめんね", reason: "safety" },
    });
    expect(
      toChatStreamEvent(message("replace", { text: "ごめんね", reason: "unknown" }))?.data,
    ).toEqual({ text: "ごめんね", reason: "moderated" });
    expect(
      toChatStreamEvent(
        message("error", { code: "llm_unavailable", message: "混雑", request_id: "r" }),
      ),
    ).toEqual({
      type: "error",
      data: { code: "llm_unavailable", message: "混雑", request_id: "r" },
    });
  });

  it("done は ChatResponse に正規化する（is_proactive の欠落・名前の無い窓口を補正）", () => {
    const event = toChatStreamEvent(message("done", doneData));
    expect(event?.type).toBe("done");
    if (event?.type !== "done") return;
    expect(event.data.character_message.is_proactive).toBe(false);
    expect(event.data.user_message.body).toBe("ただいま");
    expect(event.data.safety).toEqual({
      triggered: true,
      resources: [
        { name: "よりそいホットライン", phone: "0120-279-338", hours: "24時間", url: null },
      ],
    });
  });

  it("未知のイベント・JSON でない data・形の合わない done は無視する（null）", () => {
    expect(toChatStreamEvent(message("ping", {}))).toBeNull();
    expect(toChatStreamEvent(message("delta", "not json"))).toBeNull();
    expect(toChatStreamEvent(message("delta", { text: 1 }))).toBeNull();
    expect(toChatStreamEvent(message("done", { reply: "x" }))).toBeNull();
  });
});
