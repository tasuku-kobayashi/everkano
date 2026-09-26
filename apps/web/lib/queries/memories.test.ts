import type { MemoryDTO } from "@everkano/shared";
import { MutationObserver, QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { queryKeys } from "./keys";
import {
  buildTempMemory,
  createMemoryMutationOptions,
  EDITABLE_MEMORY_KINDS,
  filterMemoriesByKind,
  isNoticeWorthyMemory,
  memoryKindLabel,
  MEMORY_KIND_OPTIONS,
  presentMemoryKinds,
  splitMemoriesByStatus,
  memoryOriginLabel,
  replaceTempMemory,
  stashMemoryDraft,
  takeMemoryDraft,
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
    kind: tags.includes("summary") ? "summary" : "fact",
    status: "active",
    superseded_by: null,
    superseded_at: null,
    last_referenced_at: null,
    reference_count: 0,
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

describe("memoryOriginLabel", () => {
  const base = memory("m", 0.6, "2026-09-25T00:00:00Z");

  it("ユーザーが追加した記憶は「あなたが追加」（編集済みではない）", () => {
    const added = buildTempMemory({ character_id: "c", content: "誕生日は3月3日" }, "temp-1");
    expect(memoryOriginLabel(added)).toBe("あなたが追加");
    expect(memoryOriginLabel({ ...base, is_user_edited: true, source_message_id: null })).toBe(
      "あなたが追加",
    );
  });

  it("会話から覚えた記憶を編集したら「編集済み」、そのままならバッジなし", () => {
    const extracted = { ...base, source_message_id: "msg-1" };
    expect(memoryOriginLabel(extracted)).toBeNull();
    expect(memoryOriginLabel({ ...extracted, is_user_edited: true })).toBe("編集済み");
    expect(memoryOriginLabel(patchMemory([extracted], "m", { importance: 0.9 })[0]!)).toBe(
      "編集済み",
    );
  });

  it("要約（中期メモリ）を編集したら「編集済み」", () => {
    expect(
      memoryOriginLabel({
        ...base,
        tags: ["summary"],
        is_user_edited: true,
        source_message_id: null,
      }),
    ).toBe("編集済み");
  });
});

describe("replaceTempMemory", () => {
  const created = { ...memory("m-new", 0.6, "2026-09-25T00:00:00Z"), is_user_edited: true };

  it("仮の記憶を保存済みの記憶に置き換える", () => {
    const temp = buildTempMemory({ character_id: "c", content: "x" }, "temp-1");
    const other = memory("a", 0.3, "2026-09-24T00:00:00Z");
    expect(replaceTempMemory([temp, other], "temp-1", created)).toEqual([created, other]);
  });

  it("仮の記憶が既に無ければ（他の操作の取り消しで消えた）先頭に加える", () => {
    const other = memory("a", 0.3, "2026-09-24T00:00:00Z");
    expect(replaceTempMemory([other], "temp-1", created)).toEqual([created, other]);
    expect(replaceTempMemory([created, other], "temp-1", created)).toEqual([created, other]);
  });
});

describe("useCreateMemory（楽観的更新と失敗時の取り消し）", () => {
  const CHAR = "22222222-2222-4222-8222-222222222222";
  const request = { character_id: CHAR, content: "大事なことを覚えてほしい", importance: 0.6 };

  async function flush(): Promise<void> {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  }

  it("一覧を読み込めていない状態で追加に失敗しても、「保存中…」の仮の記憶を残さない", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = queryKeys.memories(CHAR);
    // 一覧の取得は失敗している（API 停止中）
    const list = new QueryObserver<MemoryDTO[]>(queryClient, {
      queryKey: key,
      queryFn: () => Promise.reject(new Error("network")),
    });
    const unsubscribe = list.subscribe(() => undefined);
    await flush();
    expect(list.getCurrentResult().isError).toBe(true);
    expect(queryClient.getQueryData(key)).toBeUndefined();

    let fail: (error: Error) => void = () => undefined;
    const mutation = new MutationObserver(queryClient, {
      ...createMemoryMutationOptions(
        queryClient,
        CHAR,
        () => new Promise<MemoryDTO>((_, reject) => (fail = reject)),
      ),
    });
    const pending = mutation.mutate({ request, tempId: "temp-x" }).catch(() => undefined);
    await flush();
    expect(queryClient.getQueryData<MemoryDTO[]>(key)?.map((m) => m.id)).toEqual(["temp-x"]);
    fail(new Error("down"));
    await pending;
    await flush();

    expect(
      queryClient.getQueryData<MemoryDTO[]>(key)?.some((m) => m.id === "temp-x") ?? false,
      "仮の記憶が消えている",
    ).toBe(false);
    unsubscribe();
    queryClient.clear();
  });

  it("一覧がある状態で失敗したら仮の記憶だけを取り除く（他の記憶はそのまま）", async () => {
    const queryClient = new QueryClient();
    const key = queryKeys.memories(CHAR);
    const existing = memory("a", 0.6, "2026-09-24T00:00:00Z");
    queryClient.setQueryData<MemoryDTO[]>(key, [existing]);
    const mutation = new MutationObserver(queryClient, {
      ...createMemoryMutationOptions(queryClient, CHAR, () => Promise.reject(new Error("down"))),
    });
    await mutation.mutate({ request, tempId: "temp-y" }).catch(() => undefined);
    expect(queryClient.getQueryData<MemoryDTO[]>(key)).toEqual([existing]);
    queryClient.clear();
  });

  it("成功したら仮の記憶を保存済みの記憶に置き換える", async () => {
    const queryClient = new QueryClient();
    const key = queryKeys.memories(CHAR);
    queryClient.setQueryData<MemoryDTO[]>(key, []);
    const saved = { ...memory("m-1", 0.6, "2026-09-25T00:00:00Z"), is_user_edited: true };
    const mutation = new MutationObserver(queryClient, {
      ...createMemoryMutationOptions(queryClient, CHAR, async () => saved),
    });
    await mutation.mutate({ request, tempId: "temp-z" });
    expect(queryClient.getQueryData<MemoryDTO[]>(key)?.[0]).toEqual(saved);
    queryClient.clear();
  });
});

describe("stashMemoryDraft / takeMemoryDraft", () => {
  it("預けた入力内容を 1 回だけ取り出せる（キャラごと）", () => {
    const queryClient = new QueryClient();
    const draft = {
      content: "来週プレゼン",
      level: "high" as const,
      secret: true,
      kind: "promise" as const,
    };
    stashMemoryDraft(queryClient, "c1", draft);
    expect(takeMemoryDraft(queryClient, "c2")).toBeUndefined();
    expect(takeMemoryDraft(queryClient, "c1")).toEqual(draft);
    expect(takeMemoryDraft(queryClient, "c1")).toBeUndefined();
    // もう一度預けられる（表示中のフォームが購読しているキャッシュに入る）
    stashMemoryDraft(queryClient, "c1", { ...draft, content: "2 回目" });
    expect(takeMemoryDraft(queryClient, "c1")?.content).toBe("2 回目");
    queryClient.clear();
  });
});

describe("記憶の種類（M2）", () => {
  it("表示名と、追加・編集で選べる種類（会話の要約は選べない）", () => {
    expect(MEMORY_KIND_OPTIONS.map((o) => o.label)).toEqual([
      "事実",
      "好み",
      "出来事",
      "約束・予定",
      "気持ち",
      "ふたりの関係",
      "会話の要約",
    ]);
    expect(EDITABLE_MEMORY_KINDS.map((o) => o.kind)).not.toContain("summary");
    expect(memoryKindLabel("relationship")).toBe("ふたりの関係");
  });

  it("種類で絞り込み、記憶のある種類だけをチップにする（選択中の種類は 0 件でも残す）", () => {
    const list = [
      { ...memory("a", 0.5, "2026-09-25T00:00:00Z"), kind: "preference" as const },
      { ...memory("b", 0.5, "2026-09-25T00:00:00Z"), kind: "fact" as const },
      { ...memory("c", 0.5, "2026-09-25T00:00:00Z"), kind: "preference" as const },
    ];
    expect(filterMemoriesByKind(list, "preference").map((m) => m.id)).toEqual(["a", "c"]);
    expect(filterMemoriesByKind(list, "all")).toHaveLength(3);
    expect(presentMemoryKinds(list).map((o) => o.kind)).toEqual(["fact", "preference"]);
    expect(presentMemoryKinds(list, "emotion").map((o) => o.kind)).toEqual([
      "fact",
      "preference",
      "emotion",
    ]);
  });

  it("buildTempMemory は指定の種類（既定は事実）の有効な記憶", () => {
    expect(buildTempMemory({ character_id: "c", content: "x" }, "temp-1")).toMatchObject({
      kind: "fact",
      status: "active",
    });
    expect(
      buildTempMemory({ character_id: "c", content: "x", kind: "preference" }, "temp-2").kind,
    ).toBe("preference");
  });

  it("patchMemory は種類も変える", () => {
    const [patched] = patchMemory([memory("a", 0.5, "2026-09-25T00:00:00Z")], "a", {
      kind: "emotion",
    });
    expect(patched?.kind).toBe("emotion");
    expect(patched?.is_user_edited).toBe(true);
  });

  it("kind = summary は要約として扱う（タグが無くても）", () => {
    expect(isSummaryMemory({ tags: [], kind: "summary" })).toBe(true);
    expect(isSummaryMemory({ tags: [], kind: "fact" })).toBe(false);
  });
});

describe("splitMemoriesByStatus（以前の記憶）", () => {
  it("今の記憶と置き換えられた記憶に分け、以前の記憶は置き換わった新しい順", () => {
    const old1 = {
      ...memory("o1", 0.5, "2026-09-01T00:00:00Z"),
      status: "superseded" as const,
      superseded_at: "2026-09-10T00:00:00Z",
      superseded_by: "n1",
    };
    const old2 = {
      ...memory("o2", 0.5, "2026-09-02T00:00:00Z"),
      status: "superseded" as const,
      superseded_at: "2026-09-20T00:00:00Z",
      superseded_by: "n2",
    };
    const n1 = memory("n1", 0.5, "2026-09-10T00:00:00Z");
    const { active, superseded } = splitMemoriesByStatus([old1, n1, old2]);
    expect(active.map((m) => m.id)).toEqual(["n1"]);
    expect(superseded.map((m) => m.id)).toEqual(["o2", "o1"]);
  });
});

describe("isNoticeWorthyMemory（「覚えました」を出す記憶）", () => {
  it("自動で覚えた有効な記憶だけ（要約・自分で追加した記憶・形の違う行は除く）", () => {
    expect(isNoticeWorthyMemory({ id: "m", kind: "fact", status: "active" })).toBe(true);
    expect(isNoticeWorthyMemory({ id: "m" })).toBe(true);
    expect(isNoticeWorthyMemory({ id: "m", kind: "summary" })).toBe(false);
    expect(isNoticeWorthyMemory({ id: "m", tags: ["summary"] })).toBe(false);
    expect(isNoticeWorthyMemory({ id: "m", kind: "fact", is_user_edited: true })).toBe(false);
    expect(isNoticeWorthyMemory({ id: "m", status: "superseded" })).toBe(false);
    expect(isNoticeWorthyMemory({ kind: "fact" })).toBe(false);
    expect(isNoticeWorthyMemory(null)).toBe(false);
  });
});
