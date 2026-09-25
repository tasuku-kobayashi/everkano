import type { ChatResponse } from "@everkano/shared";
import { MutationObserver, QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import {
  chatMutationKey,
  restoreSends,
  sendErrorMessage,
  waitForMutation,
  type ChatVariables,
} from "./use-send-message";

const CONV = "33333333-3333-4333-8333-333333333333";

function vars(localId: string, body = `body-${localId}`): ChatVariables {
  return { localId, body, createdAt: "2026-09-25T03:00:00.000Z", afterCreatedAt: null };
}

type State = Parameters<typeof restoreSends>[0][number]["state"];

function mutation(
  status: State["status"],
  variables: ChatVariables,
  submittedAt: number,
  error: unknown = null,
) {
  return { state: { status, variables, submittedAt, error } as State };
}

describe("restoreSends（開き直した画面が引き継ぐ送信）", () => {
  it("返答待ちは「送信中」、失敗は「送信できませんでした」として、送信順に復元する", () => {
    const failed = mutation(
      "error",
      vars("l1"),
      1,
      new ApiError({ status: 429, code: "rate_limited", message: "x" }),
    );
    const pending = mutation("pending", vars("l2", "大事な相談があるんだけど"), 2);
    const restored = restoreSends([pending, failed]);
    expect(restored.map((r) => [r.local.localId, r.local.status])).toEqual([
      ["l1", "failed"],
      ["l2", "sending"],
    ]);
    expect(restored[0]?.local.error).toBe(sendErrorMessage(failed.state.error));
    expect(restored[0]?.pending).toBeUndefined();
    expect(restored[1]?.pending).toBe(pending);
    expect(restored[1]?.local.body).toBe("大事な相談があるんだけど");
  });

  it("成功した送信は引き継がない（保存済みのメッセージとして表示される）", () => {
    expect(restoreSends([mutation("success", vars("l1"), 1)])).toEqual([]);
  });

  it("再送した発言は最新の試行だけを見る（失敗 → 再送中なら送信中、失敗 → 再送成功なら引き継がない）", () => {
    const first = mutation("error", vars("l1"), 1, new Error("network"));
    expect(
      restoreSends([first, mutation("pending", vars("l1"), 2)]).map((r) => r.local.status),
    ).toEqual(["sending"]);
    expect(restoreSends([mutation("success", vars("l1"), 3), first])).toEqual([]);
  });
});

describe("waitForMutation", () => {
  const response = { message_id: "m1" } as ChatResponse;

  it("別の画面インスタンスが始めた送信の完了（成功・失敗）を待てる", async () => {
    const queryClient = new QueryClient();
    let finish: (value: ChatResponse) => void = () => undefined;
    const observer = new MutationObserver<ChatResponse, unknown, ChatVariables>(queryClient, {
      mutationKey: chatMutationKey(CONV),
      mutationFn: () => new Promise<ChatResponse>((resolve) => (finish = resolve)),
    });
    void observer.mutate(vars("l1"));
    const [started] = queryClient
      .getMutationCache()
      .findAll({ mutationKey: chatMutationKey(CONV), exact: true });
    expect(started?.state.status).toBe("pending");

    const waiting = waitForMutation(queryClient.getMutationCache(), started!);
    for (let i = 0; i < 10; i += 1) await Promise.resolve(); // mutationFn が呼ばれるまで
    finish(response);
    await expect(waiting).resolves.toBe(response);
    // 完了済みならすぐに結果を返す
    await expect(waitForMutation(queryClient.getMutationCache(), started!)).resolves.toBe(response);
    queryClient.clear();
  });

  it("失敗したら同じエラーで reject する", async () => {
    const queryClient = new QueryClient();
    const error = new ApiError({
      status: 0,
      code: "network_error",
      message: "通信できませんでした",
    });
    const observer = new MutationObserver<ChatResponse, unknown, ChatVariables>(queryClient, {
      mutationKey: chatMutationKey(CONV),
      mutationFn: () => Promise.reject(error),
    });
    const [started] = await Promise.all([
      Promise.resolve().then(
        () =>
          queryClient
            .getMutationCache()
            .findAll({ mutationKey: chatMutationKey(CONV), exact: true })[0]!,
      ),
      observer.mutate(vars("l1")).catch(() => undefined),
    ]);
    await expect(waitForMutation(queryClient.getMutationCache(), started)).rejects.toBe(error);
    queryClient.clear();
  });
});
