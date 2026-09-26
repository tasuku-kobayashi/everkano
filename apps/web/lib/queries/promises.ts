/**
 * メモリパネルの「約束・予定」（エンジン v1.0 §4 M6 / §5 C8）。
 *
 * - 一覧は Python API GET /promises?character_id=（未達・キャラが話題にした約束だけ）。
 *   変更（完了・取り消し）は PATCH /promises/{id}（監査ログ promise.status_change）。
 * - 並びは「これからの約束（期日の近い順）→ 日付未定 → 期日を過ぎた約束（新しい順）」。
 * - 期日は日本時間の相対表現（今日・明日・あさって・3日後・来週・来月…）で表示する。
 * - 完了・取り消しは楽観的に一覧から消し、失敗したら元の位置に戻す。
 */

import type { PromiseDTO, UpdatePromiseRequest } from "@everkano/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { calendarDayDiff, zonedParts, type ZonedParts } from "@/lib/format";
import { queryKeys } from "@/lib/queries/keys";

const WEEKDAYS_JA = ["日", "月", "火", "水", "木", "金", "土"];

export interface PromiseDue {
  /** 主な表示（「今日 15:00」「明日」「3日後」「来週」「日付未定」など） */
  label: string;
  /** 補足の日付（「9月29日（火）」。相対表現だけで日付が分かる場合・日付が無い場合は null） */
  date: string | null;
  /** 期日を過ぎている */
  overdue: boolean;
}

function dateText(parts: ZonedParts, now: ZonedParts): string {
  const prefix = parts.year === now.year ? "" : `${parts.year}年`;
  return `${prefix}${parts.month}月${parts.day}日（${WEEKDAYS_JA[parts.weekday]}）`;
}

/** 近い日の言い方（それ以外は null） */
function dayWord(diff: number): string | null {
  switch (diff) {
    case 0:
      return "今日";
    case 1:
      return "明日";
    case 2:
      return "あさって";
    case -1:
      return "昨日";
    case -2:
      return "おととい";
    default:
      return null;
  }
}

/** その週の月曜日（UTC の暦日として比較に使う） */
function mondayOf(parts: ZonedParts): number {
  const day = Date.UTC(parts.year, parts.month - 1, parts.day);
  const offset = (parts.weekday + 6) % 7; // 月曜 = 0
  return day - offset * 86_400_000;
}

/**
 * 期日の表示（日本時間）。due_precision に合わせて粒度を変える:
 * - datetime: 「今日 15:00」「明日 9:30」「10月2日（金）15:00」
 * - day: 「今日」「明日」「あさって」「3日後」「昨日」「4日前」、それより先・前は日付
 * - week: 「今週」「来週」「先週」「10月5日の週」
 * - month: 「今月」「来月」「先月」「12月」
 * - unknown・期日なし: 「日付未定」
 */
export function formatPromiseDue(
  promise: Pick<PromiseDTO, "due_at" | "due_precision">,
  now: Date = new Date(),
): PromiseDue {
  const due = promise.due_at ? new Date(promise.due_at) : null;
  if (!due || Number.isNaN(due.getTime()) || promise.due_precision === "unknown") {
    return { label: "日付未定", date: null, overdue: false };
  }
  const p = zonedParts(due);
  const n = zonedParts(now);
  const diff = calendarDayDiff(n, p);

  switch (promise.due_precision) {
    case "datetime": {
      const time = `${p.hour}:${String(p.minute).padStart(2, "0")}`;
      const word = dayWord(diff);
      return {
        label: word ? `${word} ${time}` : `${dateText(p, n)} ${time}`,
        date: word && Math.abs(diff) >= 2 ? dateText(p, n) : null,
        overdue: due.getTime() < now.getTime(),
      };
    }
    case "week": {
      const weeks = Math.round((mondayOf(p) - mondayOf(n)) / (7 * 86_400_000));
      const monday = new Date(mondayOf(p));
      const label =
        weeks === 0
          ? "今週"
          : weeks === 1
            ? "来週"
            : weeks === -1
              ? "先週"
              : `${monday.getUTCMonth() + 1}月${monday.getUTCDate()}日の週`;
      return { label, date: null, overdue: weeks < 0 };
    }
    case "month": {
      const months = p.year * 12 + p.month - (n.year * 12 + n.month);
      const label =
        months === 0
          ? "今月"
          : months === 1
            ? "来月"
            : months === -1
              ? "先月"
              : p.year === n.year
                ? `${p.month}月`
                : `${p.year}年${p.month}月`;
      return { label, date: null, overdue: months < 0 };
    }
    default: {
      // day（と未知の粒度）
      const word = dayWord(diff);
      const label =
        word ??
        (diff >= 3 && diff <= 6
          ? `${diff}日後`
          : diff <= -3 && diff >= -6
            ? `${-diff}日前`
            : dateText(p, n));
      // 「あさって」「3日後」のように日付が分かりにくい言い方には日付を添える
      const absolute = dateText(p, n);
      return {
        label,
        date: label !== absolute && Math.abs(diff) >= 2 ? absolute : null,
        overdue: diff < 0,
      };
    }
  }
}

/** 並び: これから（期日の近い順）→ 日付未定 → 期日を過ぎたもの（新しい順） */
export function sortPromises(
  promises: readonly PromiseDTO[],
  now: Date = new Date(),
): PromiseDTO[] {
  const time = (p: PromiseDTO) => (p.due_at ? Date.parse(p.due_at) : Number.NaN);
  const bucket = (p: PromiseDTO) => {
    if (p.due_at === null || p.due_precision === "unknown" || Number.isNaN(time(p))) return 1;
    return formatPromiseDue(p, now).overdue ? 2 : 0;
  };
  return [...promises].sort((a, b) => {
    const ba = bucket(a);
    const bb = bucket(b);
    if (ba !== bb) return ba - bb;
    if (ba === 0) return time(a) - time(b) || (a.created_at < b.created_at ? -1 : 1);
    if (ba === 2) return time(b) - time(a) || (a.created_at < b.created_at ? 1 : -1);
    return a.created_at < b.created_at ? 1 : -1;
  });
}

// ---------------------------------------------------------------------------
// React Query
// ---------------------------------------------------------------------------

/** そのキャラとの約束（未達・話題にしたもの） */
export function usePromises(characterId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.promises(characterId),
    queryFn: async ({ signal }) => (await api.listPromises(characterId, { signal })).promises,
    enabled,
    // パネルを開くたびに最新化（会話の後で約束が増えている・キャラが話題にしている）
    staleTime: 0,
  });
}

interface PromisesContext {
  previous: PromiseDTO[] | undefined;
  removed: PromiseDTO | undefined;
  index: number;
}

/** 約束を完了・取り消しにする（PATCH /promises/{id}）。一覧から消し、失敗したら元の位置に戻す */
export function useUpdatePromise(characterId: string) {
  const queryClient = useQueryClient();
  const key = queryKeys.promises(characterId);
  return useMutation<
    PromiseDTO | null,
    unknown,
    { promiseId: string; status: UpdatePromiseRequest["status"] },
    PromisesContext
  >({
    mutationKey: [...key, "mutation"],
    mutationFn: ({ promiseId, status }) => api.updatePromise(promiseId, { status }),
    onMutate: async ({ promiseId }) => {
      await queryClient.cancelQueries({ queryKey: key, exact: true });
      const previous = queryClient.getQueryData<PromiseDTO[]>(key);
      const index = previous?.findIndex((p) => p.id === promiseId) ?? -1;
      const removed = index >= 0 ? previous?.[index] : undefined;
      queryClient.setQueryData<PromiseDTO[]>(key, (list) =>
        list?.filter((p) => p.id !== promiseId),
      );
      return { previous, removed, index };
    },
    onError: (_error, _variables, context) => {
      const removed = context?.removed;
      if (!removed) return;
      // 並行して消した他の約束は戻さず、失敗した 1 件だけを元の位置に戻す
      queryClient.setQueryData<PromiseDTO[]>(key, (list) => {
        if (!list || list.some((p) => p.id === removed.id)) return list;
        const next = [...list];
        next.splice(Math.min(context.index, next.length), 0, removed);
        return next;
      });
    },
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey: [...key, "mutation"] }) === 1) {
        void queryClient.invalidateQueries({ queryKey: key, exact: true });
      }
    },
  });
}
