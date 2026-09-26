/**
 * E6: 相談窓口の一覧（安全対応をしたキャラの返答の下に出すカード）。
 *
 * どの返答が安全対応だったかは、サーバーが返答の行に残す印（messages.safety_triggered）で分かる。
 * 履歴の読み込み・別の端末・アプリの入れ直しでも同じ返答の下にカードを出せる（端末に覚えておく必要はない）。
 * - 窓口の一覧は GET /safety/resources（全員に同じ内容。1 回取れば使い回す）
 * - 返答を受け取った端末では ChatResponse.safety.resources を先にキャッシュへ入れる（取りに行かずにすぐ出す）
 */

import type { SafetyResource } from "@everkano/shared";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/queries/keys";

/** 返答で受け取った相談窓口をキャッシュに入れる（送信のミューテーションから呼ぶ。空なら何もしない） */
export function seedSafetyResources(
  queryClient: QueryClient,
  resources: readonly SafetyResource[],
): void {
  if (resources.length === 0) return;
  queryClient.setQueryData<SafetyResource[]>(queryKeys.safetyResources(), [...resources]);
}

export interface SafetyResourcesState {
  resources: readonly SafetyResource[] | undefined;
  loading: boolean;
  error: boolean;
  retry: () => void;
}

/**
 * 相談窓口の一覧。enabled（画面に安全対応の返答がある）のときだけ取りに行く。
 * 内容は運用者が公開前に確認した固定の一覧で、会話中に変わらないので取り直さない。
 */
export function useSafetyResources(enabled: boolean): SafetyResourcesState {
  const query = useQuery({
    queryKey: queryKeys.safetyResources(),
    queryFn: async ({ signal }) => (await api.getSafetyResources({ signal })).resources,
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
  });
  return {
    resources: query.data,
    loading: enabled && query.data === undefined && !query.isError,
    error: query.isError && query.data === undefined,
    retry: () => void query.refetch(),
  };
}
