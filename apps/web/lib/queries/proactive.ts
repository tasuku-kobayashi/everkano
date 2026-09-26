/**
 * キャラからの自発メッセージの設定（エンジン v1.0 §7 / E4 / P3）。
 *
 * - 読み書きとも Python API（GET / PUT /proactive/settings、PUT /proactive/settings/{character_id}）。
 *   変更は監査ログ（proactive.settings_update）に残すため API 経由にする。
 * - 全体のオン・オフと「送らない時間帯」（JST の時。既定 0〜7 時。開始 = 終了なら制限なし）は /me、
 *   キャラ別のオン・オフは DM の「i」（メモリパネル）の先頭で変える。
 * - 変更は楽観的更新。失敗したら変えた項目だけ元に戻し（並行して進んでいる別の変更は消さない）、トーストで知らせる。
 *   最後の変更が終わったら取り直してサーバーの値にそろえる。
 */

import type {
  ProactiveCharacterSetting,
  ProactiveGlobalSettings,
  ProactiveSettingsResponse,
  UpdateProactiveGlobalSettingsRequest,
} from "@everkano/shared";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/client";
import { getErrorMessage } from "@/lib/api/errors";
import { queryKeys } from "@/lib/queries/keys";

/** 送らない時間帯の既定（仕様 P3: 0 時〜7 時） */
export const DEFAULT_QUIET_START = 0;
export const DEFAULT_QUIET_END = 7;

export const DEFAULT_GLOBAL_SETTINGS: ProactiveGlobalSettings = {
  enabled: true,
  quiet_start: DEFAULT_QUIET_START,
  quiet_end: DEFAULT_QUIET_END,
};

/** 0〜23 時 */
export const QUIET_HOURS: readonly number[] = Array.from({ length: 24 }, (_, hour) => hour);

const SAVE_FAILED_MESSAGE = "設定を保存できませんでした。しばらくしてから再度お試しください。";

// ---------------------------------------------------------------------------
// 純粋関数
// ---------------------------------------------------------------------------

/** そのキャラからの自発メッセージがオンか（行が無いキャラはオン） */
export function isCharacterProactiveEnabled(
  settings: Pick<ProactiveSettingsResponse, "characters"> | undefined,
  characterId: string,
): boolean {
  return settings?.characters.find((c) => c.character_id === characterId)?.enabled ?? true;
}

export function applyGlobalPatch(
  settings: ProactiveSettingsResponse,
  patch: UpdateProactiveGlobalSettingsRequest,
): ProactiveSettingsResponse {
  return {
    ...settings,
    global: {
      ...settings.global,
      ...(patch.enabled !== undefined ? { enabled: patch.enabled } : {}),
      ...(patch.quiet_start !== undefined ? { quiet_start: patch.quiet_start } : {}),
      ...(patch.quiet_end !== undefined ? { quiet_end: patch.quiet_end } : {}),
    },
  };
}

/** 変える前の値（失敗したときにその項目だけ戻すため） */
export function inverseGlobalPatch(
  settings: ProactiveSettingsResponse,
  patch: UpdateProactiveGlobalSettingsRequest,
): UpdateProactiveGlobalSettingsRequest {
  const inverse: UpdateProactiveGlobalSettingsRequest = {};
  if (patch.enabled !== undefined) inverse.enabled = settings.global.enabled;
  if (patch.quiet_start !== undefined) inverse.quiet_start = settings.global.quiet_start;
  if (patch.quiet_end !== undefined) inverse.quiet_end = settings.global.quiet_end;
  return inverse;
}

export function applyCharacterPatch(
  settings: ProactiveSettingsResponse,
  characterId: string,
  enabled: boolean,
): ProactiveSettingsResponse {
  const exists = settings.characters.some((c) => c.character_id === characterId);
  const characters: ProactiveCharacterSetting[] = exists
    ? settings.characters.map((c) => (c.character_id === characterId ? { ...c, enabled } : c))
    : [...settings.characters, { character_id: characterId, enabled }];
  return { ...settings, characters };
}

/** 「7:00」 */
export function formatHour(hour: number): string {
  return `${hour}:00`;
}

/**
 * 送らない時間帯の説明（JST）。開始 = 終了は制限なし。日付をまたぐ場合は「23:00〜翌7:00」。
 */
export function describeQuietHours(start: number, end: number): string {
  if (start === end) return "時間帯の制限なし";
  return `${formatHour(start)}〜${start > end ? "翌" : ""}${formatHour(end)}`;
}

// ---------------------------------------------------------------------------
// React Query
// ---------------------------------------------------------------------------

const settingsMutationKey = () => [...queryKeys.proactiveSettings(), "mutation"] as const;

/** 自発メッセージの設定（GET /proactive/settings） */
export function useProactiveSettings(enabled = true) {
  return useQuery({
    queryKey: queryKeys.proactiveSettings(),
    queryFn: ({ signal }) => api.getProactiveSettings({ signal }),
    enabled,
    staleTime: 60_000,
  });
}

/** 最後の変更が終わったら取り直す（途中で古いサーバーの値に戻って見えるのを防ぐ） */
function settle(queryClient: QueryClient) {
  if (queryClient.isMutating({ mutationKey: settingsMutationKey() }) === 1) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.proactiveSettings(), exact: true });
  }
}

/** 変更の途中（別の変更が進行中）でなければ、応答の設定をそのまま使う */
function acceptResponse(queryClient: QueryClient, response: ProactiveSettingsResponse | null) {
  if (response && queryClient.isMutating({ mutationKey: settingsMutationKey() }) === 1) {
    queryClient.setQueryData(queryKeys.proactiveSettings(), response);
  }
}

interface GlobalContext {
  inverse: UpdateProactiveGlobalSettingsRequest | null;
}

/** 全体の設定を変更（PUT /proactive/settings）。失敗はトーストで知らせ、変えた項目だけ戻す */
export function useUpdateProactiveGlobal() {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation<
    ProactiveSettingsResponse | null,
    unknown,
    UpdateProactiveGlobalSettingsRequest,
    GlobalContext
  >({
    mutationKey: settingsMutationKey(),
    mutationFn: (patch) => api.updateProactiveSettings(patch),
    onMutate: async (patch) => {
      const key = queryKeys.proactiveSettings();
      await queryClient.cancelQueries({ queryKey: key, exact: true });
      const current = queryClient.getQueryData<ProactiveSettingsResponse>(key);
      if (!current) return { inverse: null };
      queryClient.setQueryData(key, applyGlobalPatch(current, patch));
      return { inverse: inverseGlobalPatch(current, patch) };
    },
    onSuccess: (response) => acceptResponse(queryClient, response),
    onError: (error, _patch, context) => {
      const inverse = context?.inverse;
      if (inverse) {
        queryClient.setQueryData<ProactiveSettingsResponse>(
          queryKeys.proactiveSettings(),
          (current) => (current ? applyGlobalPatch(current, inverse) : current),
        );
      }
      toast.error(getErrorMessage(error, SAVE_FAILED_MESSAGE));
    },
    onSettled: () => settle(queryClient),
  });
}

interface CharacterContext {
  previous: boolean | null;
}

/** キャラ別のオン・オフ（PUT /proactive/settings/{character_id}） */
export function useUpdateProactiveCharacter(characterId: string) {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation<ProactiveSettingsResponse | null, unknown, boolean, CharacterContext>({
    mutationKey: settingsMutationKey(),
    mutationFn: (enabled) => api.updateProactiveCharacterSetting(characterId, { enabled }),
    onMutate: async (enabled) => {
      const key = queryKeys.proactiveSettings();
      await queryClient.cancelQueries({ queryKey: key, exact: true });
      const current = queryClient.getQueryData<ProactiveSettingsResponse>(key);
      if (!current) return { previous: null };
      queryClient.setQueryData(key, applyCharacterPatch(current, characterId, enabled));
      return { previous: isCharacterProactiveEnabled(current, characterId) };
    },
    onSuccess: (response) => acceptResponse(queryClient, response),
    onError: (error, _enabled, context) => {
      const previous = context?.previous;
      if (previous !== null && previous !== undefined) {
        queryClient.setQueryData<ProactiveSettingsResponse>(
          queryKeys.proactiveSettings(),
          (current) => (current ? applyCharacterPatch(current, characterId, previous) : current),
        );
      }
      toast.error(getErrorMessage(error, SAVE_FAILED_MESSAGE));
    },
    onSettled: () => settle(queryClient),
  });
}
