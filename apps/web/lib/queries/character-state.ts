/**
 * キャラの「今の状況」（DM ヘッダーの 1 行。エンジン v1.0 §5 C5）。
 *
 * - character_states（カレンダーエンジンのスケジューラが 5 分ごとに更新）を supabase-js で直接読む。
 *   クライアントに公開されている列は character_id / status_label / busyness / updated_at だけ（RLS: 有効なキャラ）。
 * - 表示は status_label（「仕事中」「カフェでひと休み」など）。行が無い・ラベルが無いときは「アクティブ」。
 * - 画面を開いている間は 2 分ごと・画面への復帰時に取り直す。
 * - 好感度・関係の段階はここを含めどこにも表示しない（A11）。
 */

import { queryOptions, useQuery } from "@tanstack/react-query";
import { toAppError } from "@/lib/api/errors";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** 状況が分からないときの表示 */
export const DEFAULT_STATUS_LABEL = "アクティブ";

/** 画面を開いている間の取り直しの間隔（スケジューラの更新は 5 分ごと） */
export const CHARACTER_STATE_REFETCH_MS = 2 * 60_000;

/** これ以上忙しいとき（0〜3）はアバターの「アクティブ」の緑の点を出さない */
export const BUSY_THRESHOLD = 2;

export interface CharacterStatus {
  character_id: string;
  status_label: string | null;
  /** 0 = ひま 〜 3 = とても忙しい（寝ている等） */
  busyness: number;
}

export const characterStateKey = (characterId: string) =>
  [...queryKeys.characterById(characterId), "state"] as const;

/** DM ヘッダーの 2 行目に出す文言 */
export function statusLine(
  state: Pick<CharacterStatus, "status_label"> | null | undefined,
): string {
  const label = state?.status_label?.replace(/\s+/g, " ").trim();
  return label ? label : DEFAULT_STATUS_LABEL;
}

/** アバターに「アクティブ」の緑の点を出すか（状況が分からなければ出す） */
export function showsActiveDot(
  state: Pick<CharacterStatus, "busyness"> | null | undefined,
): boolean {
  return !state || !(state.busyness >= BUSY_THRESHOLD);
}

export function characterStateQueryOptions(characterId: string) {
  return queryOptions({
    queryKey: characterStateKey(characterId),
    queryFn: async ({ signal }): Promise<CharacterStatus | null> => {
      const { data, error } = await getSupabaseBrowserClient()
        .from("character_states")
        .select("character_id, status_label, busyness")
        .eq("character_id", characterId)
        .abortSignal(signal)
        .maybeSingle();
      if (error) throw toAppError(error);
      return data;
    },
    staleTime: 60_000,
    refetchInterval: CHARACTER_STATE_REFETCH_MS,
    refetchOnWindowFocus: true,
  });
}

/** キャラの今の状況（取得に失敗しても表示は「アクティブ」にするだけ） */
export function useCharacterState(characterId: string, enabled = true) {
  return useQuery({ ...characterStateQueryOptions(characterId), enabled });
}
