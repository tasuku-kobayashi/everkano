import type { MemoryDTO } from "@everkano/shared";
import { describe, expect, it } from "vitest";
import {
  buildTempMemory,
  importanceToLevel,
  isSecretMemory,
  isSummaryMemory,
  isTempMemoryId,
  levelLabel,
  levelToImportance,
  MEMORY_LEVELS,
  nextTempMemoryId,
  patchMemory,
  sortMemories,
  stableMemoryOrder,
  withTag,
} from "./memories";

function memory(id: string, importance: number, createdAt: string, tags: string[] = []): MemoryDTO {
  return {
    id,
    character_id: "c",
    content: `content-${id}`,
    importance,
    tags,
    is_user_edited: false,
    source_message_id: null,
    created_at: createdAt,
    updated_at: createdAt,
  };
}

describe("importance ↔ 優先度（低/中/高）", () => {
  it("段階 → importance", () => {
    expect(levelToImportance("low")).toBe(0.3);
    expect(levelToImportance("mid")).toBe(0.6);
    expect(levelToImportance("high")).toBe(0.9);
    expect(MEMORY_LEVELS.map((l) => l.label)).toEqual(["低", "中", "高"]);
  });

  it("保存値はそのまま同じ段階に戻る", () => {
    for (const option of MEMORY_LEVELS) {
      expect(importanceToLevel(option.importance)).toBe(option.level);
    }
  });

  it("その他の値は最も近い段階（中間点はちょうどなら上）", () => {
    expect(importanceToLevel(0)).toBe("low");
    expect(importanceToLevel(0.44)).toBe("low");
    expect(importanceToLevel(0.45)).toBe("mid");
    expect(importanceToLevel(0.3 + 0.15)).toBe("mid"); // 0.44999999999999996
    expect(importanceToLevel(0.7)).toBe("mid"); // API の既定 0.7
    expect(importanceToLevel(0.74)).toBe("mid");
    expect(importanceToLevel(0.75)).toBe("high");
    expect(importanceToLevel(1)).toBe("high");
  });

  it("不正値は中", () => {
    expect(importanceToLevel(Number.NaN)).toBe("mid");
    expect(levelLabel(importanceToLevel(Number.POSITIVE_INFINITY))).toBe("中");
  });
});

describe("タグ", () => {
  it("秘密・要約の判定", () => {
    expect(isSecretMemory({ tags: ["secret"] })).toBe(true);
    expect(isSecretMemory({ tags: [] })).toBe(false);
    expect(isSummaryMemory({ tags: ["summary", "secret"] })).toBe(true);
  });

  it("付け外し（他のタグは保ち、重複しない）", () => {
    expect(withTag(["summary"], "secret", true)).toEqual(["summary", "secret"]);
    expect(withTag(["secret", "summary"], "secret", true)).toEqual(["summary", "secret"]);
    expect(withTag(["summary", "secret"], "secret", false)).toEqual(["summary"]);
    expect(withTag([], "secret", false)).toEqual([]);
  });
});

describe("並び順", () => {
  const a = memory("a", 0.9, "2026-09-20T00:00:00Z");
  const b = memory("b", 0.6, "2026-09-24T00:00:00Z");
  const c = memory("c", 0.6, "2026-09-25T00:00:00Z");

  it("API と同じ（重要度 → 新しい順）", () => {
    expect(sortMemories([b, c, a]).map((m) => m.id)).toEqual(["a", "c", "b"]);
  });

  it("表示中は位置を保ち、新しい記憶は先頭へ・消えたものは除く", () => {
    const initial = stableMemoryOrder([], [a, c, b]);
    expect(initial).toEqual(["a", "c", "b"]);
    // b の優先度を上げてサーバーの並びが変わっても、表示位置は変わらない
    const raised = { ...b, importance: 0.9 };
    expect(stableMemoryOrder(initial, sortMemories([a, c, raised]))).toEqual(["a", "c", "b"]);
    // 追加と削除
    const d = memory("d", 0.3, "2026-09-25T01:00:00Z");
    expect(stableMemoryOrder(initial, [a, b, d])).toEqual(["d", "a", "b"]);
  });
});

describe("楽観的更新", () => {
  it("patchMemory は指定フィールドだけ変え、編集済みにする", () => {
    const list = [
      memory("a", 0.3, "2026-09-20T00:00:00Z"),
      memory("b", 0.6, "2026-09-20T00:00:00Z"),
    ];
    const next = patchMemory(list, "a", { importance: 0.9, tags: ["secret"] }, "NOW");
    expect(next[0]).toMatchObject({
      id: "a",
      importance: 0.9,
      tags: ["secret"],
      content: "content-a",
      is_user_edited: true,
      updated_at: "NOW",
    });
    expect(next[1]).toBe(list[1]);
    expect(list[0]!.importance).toBe(0.3);
  });

  it("仮の記憶", () => {
    const id = nextTempMemoryId();
    expect(isTempMemoryId(id)).toBe(true);
    expect(nextTempMemoryId()).not.toBe(id);
    const temp = buildTempMemory(
      { character_id: "c", content: "カレーが好き", importance: 0.9, tags: ["secret"] },
      id,
      "NOW",
    );
    expect(temp).toMatchObject({
      id,
      content: "カレーが好き",
      importance: 0.9,
      tags: ["secret"],
      is_user_edited: true,
      created_at: "NOW",
    });
    expect(buildTempMemory({ character_id: "c", content: "x" }, id).importance).toBe(0.7);
  });
});
