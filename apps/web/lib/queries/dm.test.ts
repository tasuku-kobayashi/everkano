import type { DmThread } from "@everkano/shared";
import { describe, expect, it } from "vitest";
import {
  excludeConversed,
  filterThreads,
  markThreadReadInList,
  threadPreview,
  totalUnread,
} from "./dm";

function thread(overrides: Partial<DmThread> = {}): DmThread {
  return {
    conversation_id: "conv-1",
    character_id: "char-1",
    character_handle: "misaki_ol",
    character_name: "美咲",
    character_avatar_url: "https://example.com/a.png",
    last_message_body: "おつかれさま",
    last_message_sender_type: "character",
    last_message_at: "2026-09-25T03:00:00Z",
    unread_count: 0,
    ...overrides,
  };
}

describe("threadPreview", () => {
  it("自分の発言には「あなた: 」を付ける", () => {
    expect(
      threadPreview(thread({ last_message_sender_type: "user", last_message_body: "やあ" })),
    ).toBe("あなた: やあ");
    expect(threadPreview(thread())).toBe("おつかれさま");
  });

  it("改行・連続空白は 1 つの空白に、空なら案内文", () => {
    expect(threadPreview(thread({ last_message_body: "一行目\n\n二行目  です" }))).toBe(
      "一行目 二行目 です",
    );
    expect(threadPreview(thread({ last_message_body: "" }))).toBe("メッセージを送信しよう");
  });
});

describe("totalUnread / markThreadReadInList", () => {
  const threads = [
    thread({ conversation_id: "a", unread_count: 2 }),
    thread({ conversation_id: "b", unread_count: 3 }),
    thread({ conversation_id: "c", unread_count: 0 }),
  ];

  it("未読合計", () => {
    expect(totalUnread(threads)).toBe(5);
    expect(totalUnread([])).toBe(0);
  });

  it("既読にした会話だけ 0 にする（変化なしなら同じ参照）", () => {
    const next = markThreadReadInList(threads, "a")!;
    expect(next.map((t) => t.unread_count)).toEqual([0, 3, 0]);
    expect(next[1]).toBe(threads[1]);
    expect(markThreadReadInList(threads, "c")).toBe(threads);
    expect(markThreadReadInList(undefined, "a")).toBeUndefined();
  });
});

describe("filterThreads / excludeConversed", () => {
  const threads = [
    thread({ character_id: "1", character_name: "美咲", character_handle: "misaki_ol" }),
    thread({ character_id: "2", character_name: "ひなた", character_handle: "hinata_umi" }),
  ];

  it("名前・ハンドルで絞り込む（大文字小文字を無視）", () => {
    expect(filterThreads(threads, "ひな").map((t) => t.character_id)).toEqual(["2"]);
    expect(filterThreads(threads, "MISAKI").map((t) => t.character_id)).toEqual(["1"]);
    expect(filterThreads(threads, "  ")).toHaveLength(2);
  });

  it("会話済みのキャラをおすすめから除く", () => {
    expect(excludeConversed([{ id: "1" }, { id: "2" }, { id: "3" }], threads)).toEqual([
      { id: "3" },
    ]);
  });
});
