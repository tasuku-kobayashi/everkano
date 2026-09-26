import type { ChatResponse, ChatStreamEvent } from "@everkano/shared";
import { describe, expect, it, vi } from "vitest";
import type { TimelineMessage } from "@/lib/queries/messages";
import {
  createChatStreamStore,
  initialStreamingReply,
  markDeliveredWhileStreaming,
  reduceChatStream,
  streamingTimelineItem,
  streamKey,
  type StreamingReply,
} from "./stream-state";

const done = (body: string, safety: ChatResponse["safety"] = null): ChatStreamEvent => ({
  type: "done",
  data: {
    message_id: "m2",
    reply: body,
    memories_used: [],
    memories_created: [],
    user_message: {
      id: "m1",
      conversation_id: "c",
      sender_type: "user",
      body: "ただいま",
      created_at: "2026-09-26T09:00:00.000001Z",
      is_proactive: false,
      safety_triggered: false,
    },
    character_message: {
      id: "m2",
      conversation_id: "c",
      sender_type: "character",
      body,
      created_at: "2026-09-26T09:00:00.000002Z",
      is_proactive: false,
      safety_triggered: false,
    },
    moderated: false,
    safety,
  },
});

function apply(events: readonly ChatStreamEvent[]): StreamingReply {
  return events.reduce(reduceChatStream, initialStreamingReply("l1", "2026-09-26T09:00:00Z"));
}

describe("reduceChatStream", () => {
  it("delta を順に追記する", () => {
    const state = apply([
      { type: "delta", data: { text: "おかえり！" } },
      { type: "delta", data: { text: "今日はどうだった？" } },
    ]);
    expect(state.text).toBe("おかえり！今日はどうだった？");
    expect(state.messageId).toBeNull();
  });

  it("replace は本文を置き換え、理由を残す", () => {
    const state = apply([
      { type: "delta", data: { text: "えっと" } },
      { type: "replace", data: { text: "その話はやめておこうかな", reason: "moderated" } },
      { type: "delta", data: { text: "ごめんね" } },
    ]);
    expect(state.text).toBe("その話はやめておこうかなごめんね");
    expect(state.replacedReason).toBe("moderated");
  });

  it("done で保存済みの本文・id・安全対応が確定し、その後の delta / replace は無視する", () => {
    const safety = {
      triggered: true,
      resources: [{ name: "いのちの電話", phone: "0570-783-556", hours: null, url: null }],
    };
    const state = apply([
      { type: "delta", data: { text: "途中" } },
      done("保存された返答", safety),
      { type: "delta", data: { text: "遅れて届いた" } },
      { type: "replace", data: { text: "x", reason: "moderated" } },
    ]);
    expect(state.text).toBe("保存された返答");
    expect(state.messageId).toBe("m2");
    expect(state.safety).toEqual(safety);
  });

  it("delta が無くても done で本文が入る（/chat へのフォールバック）", () => {
    expect(apply([done("おかえり")]).text).toBe("おかえり");
  });

  it("safety が triggered でなければ null・空の delta と error は状態を変えない", () => {
    const initial = initialStreamingReply("l1", "2026-09-26T09:00:00Z");
    expect(reduceChatStream(initial, { type: "delta", data: { text: "" } })).toBe(initial);
    expect(
      reduceChatStream(initial, {
        type: "error",
        data: { code: "llm_unavailable", message: "x" },
      }),
    ).toBe(initial);
    expect(apply([done("a", { triggered: false, resources: [] })]).safety).toBeNull();
  });
});

describe("streamingTimelineItem（受信中の吹き出し）", () => {
  const streaming: StreamingReply = {
    ...initialStreamingReply("l1", "2026-09-26T09:00:00Z"),
    text: "おかえり",
  };
  const saved: StreamingReply = { ...streaming, text: "おかえり！", messageId: "m2" };
  const none = new Set<string>();

  it("返答待ちの送信への返答: 表示してよくなってから、届いた本文を出す（それまでは入力中）", () => {
    expect(
      streamingTimelineItem(streaming, { activeLocalId: "l1", revealed: false, savedIds: none }),
    ).toBeNull();
    const item = streamingTimelineItem(streaming, {
      activeLocalId: "l1",
      revealed: true,
      savedIds: none,
    });
    expect(item).toMatchObject({
      key: streamKey("l1"),
      senderType: "character",
      body: "おかえり",
      status: "streaming",
      createdAt: "2026-09-26T09:00:00Z",
    });
  });

  it("本文がまだ無ければ出さない（入力中のまま）", () => {
    expect(
      streamingTimelineItem(
        { ...streaming, text: "" },
        { activeLocalId: "l1", revealed: true, savedIds: none },
      ),
    ).toBeNull();
  });

  it("done 後は保存済みの id・status sent になる（同じ key のまま保存済みの返答に置き換わる）", () => {
    expect(
      streamingTimelineItem(saved, { activeLocalId: "l1", revealed: true, savedIds: none }),
    ).toMatchObject({ key: streamKey("l1"), id: "m2", status: "sent", body: "おかえり！" });
  });

  it("E6: 安全対応の返答は replace（reason: safety）の時点で相談窓口のカードの印が付く", () => {
    const options = { activeLocalId: "l1", revealed: true, savedIds: none };
    expect(streamingTimelineItem(streaming, options)?.safetyTriggered).toBeUndefined();
    const replaced = reduceChatStream(streaming, {
      type: "replace",
      data: { text: "話してくれてありがとう", reason: "safety" },
    });
    expect(streamingTimelineItem(replaced, options)).toMatchObject({ safetyTriggered: true });
    const moderated = reduceChatStream(streaming, {
      type: "replace",
      data: { text: "ごめんね", reason: "moderated" },
    });
    expect(streamingTimelineItem(moderated, options)?.safetyTriggered).toBeUndefined();
  });

  it("表示を終えた後は、保存済みの返答がキャッシュに現れるまでだけ出し続ける", () => {
    expect(
      streamingTimelineItem(saved, { activeLocalId: null, revealed: false, savedIds: none })?.id,
    ).toBe("m2");
    expect(
      streamingTimelineItem(saved, {
        activeLocalId: null,
        revealed: false,
        savedIds: new Set(["m2"]),
      }),
    ).toBeNull();
    // 完了していない別の送信の状態（失敗して片付けられる前など）は出さない
    expect(
      streamingTimelineItem(streaming, { activeLocalId: "l2", revealed: true, savedIds: none }),
    ).toBeNull();
    expect(
      streamingTimelineItem(null, { activeLocalId: "l1", revealed: true, savedIds: none }),
    ).toBeNull();
  });
});

describe("createChatStreamStore", () => {
  it("会話ごとに受信中の返答を持ち、変わったら通知する", () => {
    const store = createChatStreamStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.start("c1", "l1", "2026-09-26T09:00:00Z");
    store.apply("c1", "l1", { type: "delta", data: { text: "おか" } });
    store.apply("c1", "l1", { type: "delta", data: { text: "えり" } });
    expect(store.get("c1")?.text).toBe("おかえり");
    expect(store.get("c2")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(3);

    // 別の送信のイベント・別の送信の clear は無視する
    store.apply("c1", "old", { type: "delta", data: { text: "x" } });
    store.clear("c1", "old");
    expect(store.get("c1")?.text).toBe("おかえり");
    expect(listener).toHaveBeenCalledTimes(3);

    // 状態が変わらないイベントでは通知しない
    store.apply("c1", "l1", { type: "delta", data: { text: "" } });
    expect(listener).toHaveBeenCalledTimes(3);

    store.clear("c1", "l1");
    expect(store.get("c1")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(4);
    unsubscribe();
    store.start("c1", "l2");
    expect(listener).toHaveBeenCalledTimes(4);
  });

  it("新しい送信を始めると前の返答の状態は捨てる", () => {
    const store = createChatStreamStore();
    store.start("c1", "l1");
    store.apply("c1", "l1", done("前の返答"));
    store.start("c1", "l2");
    expect(store.get("c1")).toMatchObject({ localId: "l2", text: "", messageId: null });
  });
});

describe("markDeliveredWhileStreaming", () => {
  const local = (localId: string, status: TimelineMessage["status"]): TimelineMessage => ({
    key: localId,
    id: localId,
    localId,
    senderType: "user",
    body: localId,
    createdAt: "2026-09-26T09:00:00Z",
    status,
  });

  it("返答が届き始めた送信の自分の吹き出しだけを送信済みの見た目にする", () => {
    const items = [local("l0", "failed"), local("l1", "sending")];
    expect(markDeliveredWhileStreaming(items, "l1").map((i) => i.status)).toEqual([
      "failed",
      "sent",
    ]);
    expect(markDeliveredWhileStreaming(items, null).map((i) => i.status)).toEqual([
      "failed",
      "sending",
    ]);
  });
});
