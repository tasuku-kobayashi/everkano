/**
 * 表示用フォーマッタ（Instagram 日本語版の表記に準拠）。
 * 日付は日本向けサービスのため Asia/Tokyo 固定（SSR とクライアントで結果を一致させる目的もある）。
 */

export const APP_TIME_ZONE = "Asia/Tokyo";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

type DateInput = string | number | Date;

function toDate(input: DateInput): Date {
  return input instanceof Date ? input : new Date(input);
}

/** Asia/Tokyo での日時の各部分（weekday: 0 = 日曜） */
export interface ZonedParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  weekday: number;
}

const partsFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: APP_TIME_ZONE,
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
  hourCycle: "h23",
  weekday: "short",
});

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/**
 * 日時を Asia/Tokyo の年月日・時分・曜日に分解する（端末のタイムゾーンに依存しない）。
 * 日付の表示を組み立てるときはこれを使い、Intl.DateTimeFormat を個別に作らないこと。
 */
export function zonedParts(date: Date): ZonedParts {
  const map: Record<string, string> = {};
  for (const part of partsFormatter.formatToParts(date)) map[part.type] = part.value;
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    hour: Number(map.hour),
    minute: Number(map.minute),
    weekday: WEEKDAYS.indexOf(map.weekday ?? "Sun"),
  };
}

/** 暦日の差 b − a（Asia/Tokyo 基準）。同日 0 / a が昨日なら 1 */
export function calendarDayDiff(a: ZonedParts, b: ZonedParts): number {
  const ua = Date.UTC(a.year, a.month - 1, a.day);
  const ub = Date.UTC(b.year, b.month - 1, b.day);
  return Math.round((ub - ua) / DAY);
}

function formatMonthDay(parts: ZonedParts, now: ZonedParts): string {
  return parts.year === now.year
    ? `${parts.month}月${parts.day}日`
    : `${parts.year}年${parts.month}月${parts.day}日`;
}

/**
 * 投稿・コメントの相対時刻。
 * 「たった今」「5分前」「3時間前」「2日前」、7日以上前は「9月3日」（年が違えば「2025年9月3日」）。
 * 未来の日時（端末時計のずれ）は「たった今」。
 */
export function formatRelativeTime(input: DateInput, now: DateInput = new Date()): string {
  const date = toDate(input);
  const current = toDate(now);
  if (Number.isNaN(date.getTime())) return "";
  const diff = current.getTime() - date.getTime();

  if (diff < MINUTE) return "たった今";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}分前`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}時間前`;
  if (diff < WEEK) return `${Math.floor(diff / DAY)}日前`;
  return formatMonthDay(zonedParts(date), zonedParts(current));
}

/**
 * DM 一覧などの短い相対時刻（Instagram の「5分」「3時間」「2日」「3週間」表記）。
 * 1 分未満は「今」。52 週以上は「9月3日」形式。
 */
export function formatRelativeTimeShort(input: DateInput, now: DateInput = new Date()): string {
  const date = toDate(input);
  const current = toDate(now);
  if (Number.isNaN(date.getTime())) return "";
  const diff = current.getTime() - date.getTime();

  if (diff < MINUTE) return "今";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}分`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}時間`;
  if (diff < WEEK) return `${Math.floor(diff / DAY)}日`;
  if (diff < 52 * WEEK) return `${Math.floor(diff / WEEK)}週間`;
  return formatMonthDay(zonedParts(date), zonedParts(current));
}

/** 小数第1位で切り捨て、末尾の .0 を除く */
function truncate1(value: number): string {
  const truncated = Math.floor(value * 10) / 10;
  return Number.isInteger(truncated) ? String(truncated) : truncated.toFixed(1);
}

/**
 * 件数の短縮表記（Instagram 日本語版に準拠）。
 * 1,234 / 1.2万 / 12万 / 123万 / 1.2億。切り捨て（1.29万 → 1.2万）。
 */
export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "0";
  const n = Math.max(0, Math.floor(value));
  if (n < 10_000) return n.toLocaleString("ja-JP");
  if (n < 100_000) return `${truncate1(n / 10_000)}万`;
  if (n < 100_000_000) return `${Math.floor(n / 10_000).toLocaleString("ja-JP")}万`;
  if (n < 1_000_000_000) return `${truncate1(n / 100_000_000)}億`;
  return `${Math.floor(n / 100_000_000).toLocaleString("ja-JP")}億`;
}

/**
 * 他ユーザーの匿名表示名（profiles は本人しか読めないため）。
 * `user_` + author_user_id の先頭 6 桁（ハイフン除去）。
 */
export function anonymousUserName(userId: string): string {
  return `user_${userId.replace(/-/g, "").slice(0, 6).toLowerCase()}`;
}
