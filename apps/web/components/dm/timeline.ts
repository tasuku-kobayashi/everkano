/**
 * DM 会話の表示用の行を組み立てる純粋関数（Instagram DM のグループ化・時刻区切り）。
 *
 * - 同じ送り手の連続したメッセージを 1 グループにまとめ、吹き出しの角丸を詰める
 * - キャラのグループは最後の吹き出しの横にだけアバターを出す
 * - 直前のメッセージから 15 分以上空いたら（と先頭に）時刻の区切り「今日 14:03」を入れる
 */

import type { SenderType } from "@everkano/shared";
import { timestampToMicros, type TimelineMessage } from "@/lib/queries/messages";

/** これ以上間が空いたら時刻の区切りを入れる */
export const SEPARATOR_GAP_MS = 15 * 60_000;

export interface SeparatorRow {
  kind: "separator";
  key: string;
  label: string;
}

export interface MessageRow {
  kind: "message";
  key: string;
  message: TimelineMessage;
  /** グループ（同じ送り手の連続）の先頭 */
  isFirstInGroup: boolean;
  /** グループの最後（キャラ側はここにアバターを出す） */
  isLastInGroup: boolean;
}

export type TimelineRow = SeparatorRow | MessageRow;

const TIME_ZONE = "Asia/Tokyo";
const WEEKDAYS_JA = ["日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日"];

const partsFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
  hourCycle: "h23",
});

interface ZonedParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

function zoned(date: Date): ZonedParts {
  const map: Record<string, string> = {};
  for (const part of partsFormatter.formatToParts(date)) map[part.type] = part.value;
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    hour: Number(map.hour),
    minute: Number(map.minute),
  };
}

/**
 * 区切りの表示（Asia/Tokyo）:
 * 今日「今日 14:03」/ 昨日「昨日 14:03」/ 7 日以内「火曜日 14:03」/ 今年「9月3日 14:03」/ それ以前「2025年9月3日 14:03」
 */
export function formatSeparatorLabel(input: string | Date, now: Date = new Date()): string {
  const date = typeof input === "string" ? new Date(timestampToMicros(input) / 1000) : input;
  if (Number.isNaN(date.getTime())) return "";
  const p = zoned(date);
  const n = zoned(now);
  const time = `${p.hour}:${String(p.minute).padStart(2, "0")}`;
  const dayDiff = Math.round(
    (Date.UTC(n.year, n.month - 1, n.day) - Date.UTC(p.year, p.month - 1, p.day)) / 86_400_000,
  );
  if (dayDiff <= 0) return `今日 ${time}`;
  if (dayDiff === 1) return `昨日 ${time}`;
  if (dayDiff < 7) {
    const weekday = new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay();
    return `${WEEKDAYS_JA[weekday]} ${time}`;
  }
  if (p.year === n.year) return `${p.month}月${p.day}日 ${time}`;
  return `${p.year}年${p.month}月${p.day}日 ${time}`;
}

function millis(message: TimelineMessage): number {
  return timestampToMicros(message.createdAt) / 1000;
}

/**
 * タイムライン（古い順）を表示用の行に変換する。
 * ローカル（送信中・失敗）のメッセージは端末時刻を持つため、区切りの判定では直前の行より過去に
 * なっても（時計ずれ）区切りを入れない。
 */
export function buildTimelineRows(
  messages: readonly TimelineMessage[],
  now: Date = new Date(),
): TimelineRow[] {
  const rows: TimelineRow[] = [];
  let previous: TimelineMessage | null = null;
  let previousMs = Number.NaN;

  const withSeparator: boolean[] = messages.map((message, index) => {
    const ms = millis(message);
    const needs =
      index === 0 ||
      (Number.isFinite(ms) && Number.isFinite(previousMs) && ms - previousMs >= SEPARATOR_GAP_MS);
    if (Number.isFinite(ms) && (!Number.isFinite(previousMs) || ms > previousMs)) previousMs = ms;
    return needs;
  });

  messages.forEach((message, index) => {
    const separated = withSeparator[index] ?? false;
    if (separated) {
      rows.push({
        kind: "separator",
        key: `sep-${message.key}`,
        label: formatSeparatorLabel(message.createdAt, now),
      });
    }
    const next = messages[index + 1];
    const nextSeparated = withSeparator[index + 1] ?? true;
    const isFirstInGroup =
      separated || previous === null || previous.senderType !== message.senderType;
    const isLastInGroup = !next || nextSeparated || next.senderType !== message.senderType;
    rows.push({ kind: "message", key: message.key, message, isFirstInGroup, isLastInGroup });
    previous = message;
  });

  return rows;
}

/**
 * 吹き出しの角丸（Instagram 準拠: 外側 22px、グループ内で隣接する側は 4px）。
 * 自分（右）は右側の角、キャラ（左）は左側の角を詰める。
 * Tailwind はソース中の文字列からクラスを生成するため、クラス名は動的に組み立てずに列挙する。
 */
const BUBBLE_RADIUS: Record<SenderType, Record<"single" | "first" | "middle" | "last", string>> = {
  user: {
    single: "rounded-tl-[22px] rounded-bl-[22px] rounded-tr-[22px] rounded-br-[22px]",
    first: "rounded-tl-[22px] rounded-bl-[22px] rounded-tr-[22px] rounded-br-[4px]",
    middle: "rounded-tl-[22px] rounded-bl-[22px] rounded-tr-[4px] rounded-br-[4px]",
    last: "rounded-tl-[22px] rounded-bl-[22px] rounded-tr-[4px] rounded-br-[22px]",
  },
  character: {
    single: "rounded-tr-[22px] rounded-br-[22px] rounded-tl-[22px] rounded-bl-[22px]",
    first: "rounded-tr-[22px] rounded-br-[22px] rounded-tl-[22px] rounded-bl-[4px]",
    middle: "rounded-tr-[22px] rounded-br-[22px] rounded-tl-[4px] rounded-bl-[4px]",
    last: "rounded-tr-[22px] rounded-br-[22px] rounded-tl-[4px] rounded-bl-[22px]",
  },
};

export function bubbleRadiusClass(
  sender: SenderType,
  isFirstInGroup: boolean,
  isLastInGroup: boolean,
): string {
  const position =
    isFirstInGroup && isLastInGroup
      ? "single"
      : isFirstInGroup
        ? "first"
        : isLastInGroup
          ? "last"
          : "middle";
  return BUBBLE_RADIUS[sender][position];
}

const EMOJI_ONLY_RE =
  /^(?:\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic}|\p{Emoji_Modifier})*\s*){1,3}$/u;

/** 絵文字だけ（1〜3 個）の短いメッセージは吹き出し無しで大きく表示する（Instagram と同じ） */
export function isEmojiOnly(body: string): boolean {
  const trimmed = body.trim();
  return trimmed.length > 0 && EMOJI_ONLY_RE.test(trimmed);
}

/**
 * 返答待ちの間、送信時点（holdAfter）より新しいキャラの発言を隠す。
 * Realtime や即答の API でキャラの返答が先に届いても、「入力中…」を見せてから表示するため。
 * holdAfter = null（空の会話で送信）は、キャラの発言をすべて待たせる。
 */
export function holdCharacterReplies(
  messages: readonly TimelineMessage[],
  holdAfter: string | null,
): TimelineMessage[] {
  const threshold = holdAfter === null ? Number.NEGATIVE_INFINITY : timestampToMicros(holdAfter);
  return messages.filter(
    (message) =>
      message.senderType !== "character" ||
      message.status !== "sent" ||
      !(timestampToMicros(message.createdAt) > threshold),
  );
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** URL の characterId が UUID 形式か（不正なら API を呼ばずに「見つかりません」を出す） */
export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}
