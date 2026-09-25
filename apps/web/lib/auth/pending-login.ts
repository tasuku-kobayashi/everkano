/**
 * ログイン画面の「確認コード入力待ち」の状態を端末に保存する（localStorage）。
 *
 * iOS のホーム画面 PWA はコードを読みにメールアプリへ切り替えている間にアプリを破棄しやすく、
 * 戻ると start_url（/ → /login）から起動し直す。React の state だけだと確認コードの入力画面が消え、
 * 同じメールアドレスで送り直すと Supabase の送信間隔の制限（over_email_send_rate_limit）で断られ、
 * 手元に有効なコードがあるのにログインできなくなる。
 *
 * 保存するのはメールアドレスと送信時刻だけ（コードは保存しない）。確認コードの有効期限を過ぎたもの・
 * 壊れた値は無視して消す。ログインに成功したら消す。localStorage が使えない環境（プライベートモード等）では
 * 何もしない（従来どおり React の state だけで動く）。
 */

const STORAGE_KEY = "everkano:pending-login";

/**
 * 確認コードの有効期限（infra/supabase/config.toml の [auth.email] otp_expiry = 900 秒と揃える）。
 * これを過ぎたコードは使えないため、入力画面も復元しない。
 */
export const PENDING_LOGIN_TTL_MS = 15 * 60_000;

/** 再送信までの待ち時間（秒）。ホスト版 Supabase の既定のメール送信間隔（60 秒）に合わせる */
export const RESEND_COOLDOWN_SECONDS = 60;

export interface PendingLogin {
  email: string;
  /** ログインメールを送った時刻（UNIX ミリ秒） */
  sentAt: number;
}

/** 保存値を検証して読み取る（テスト可能な純粋関数）。期限切れ・未来の時刻・形式不正は null */
export function parsePendingLogin(raw: string | null, now: number): PendingLogin | null {
  if (!raw) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const { email, sentAt } = value as { email?: unknown; sentAt?: unknown };
  if (typeof email !== "string" || email.length === 0 || email.length > 320) return null;
  if (typeof sentAt !== "number" || !Number.isFinite(sentAt)) return null;
  // 端末の時計が戻った場合（sentAt が未来）も信用しない
  if (sentAt > now + 60_000 || now - sentAt >= PENDING_LOGIN_TTL_MS) return null;
  return { email, sentAt };
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    // Safari のプライベートモードやストレージが無効な環境ではアクセスだけで例外になる
    return null;
  }
}

/** 保存されている入力待ちの状態（無ければ・期限切れなら null。期限切れの値は消す） */
export function loadPendingLogin(now: number = Date.now()): PendingLogin | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(STORAGE_KEY);
    const pending = parsePendingLogin(raw, now);
    if (!pending && raw !== null) store.removeItem(STORAGE_KEY);
    return pending;
  } catch {
    return null;
  }
}

/** ログインメールを送った（または送信済みと分かった）ことを保存する */
export function savePendingLogin(email: string, sentAt: number = Date.now()): void {
  try {
    storage()?.setItem(STORAGE_KEY, JSON.stringify({ email, sentAt } satisfies PendingLogin));
  } catch {
    // 容量超過など。保存できなくてもログインは続けられる
  }
}

/** 入力待ちの状態を消す（ログイン成功・メールアドレスの変更・退会済みの判明時） */
export function clearPendingLogin(): void {
  try {
    storage()?.removeItem(STORAGE_KEY);
  } catch {
    // 何もしない
  }
}

/** sentAt に送ったメールの再送信ができるまでの残り秒数（0 なら再送信できる） */
export function resendCooldownRemaining(
  sentAt: number,
  now: number = Date.now(),
  cooldownSeconds: number = RESEND_COOLDOWN_SECONDS,
): number {
  const elapsed = Math.floor((now - sentAt) / 1000);
  return Math.min(cooldownSeconds, Math.max(0, cooldownSeconds - elapsed));
}

/**
 * Supabase Auth の送信間隔エラー（over_email_send_rate_limit）の本文から、あと何秒で送れるかを読む。
 * GoTrue の本文は "For security purposes, you can only request this after 42 seconds."。
 * この形式なら「このアドレスには直前にメールを送っている」（= 手元に有効なコードがある）ことも分かる。
 * 読み取れない（プロジェクト全体の 1 時間あたりの上限など）場合は null。
 */
export function parseResendWaitSeconds(message: string | undefined | null): number | null {
  const match = /after\s+(\d+)\s+seconds?/i.exec(message ?? "");
  if (!match) return null;
  const seconds = Number.parseInt(match[1] ?? "", 10);
  return Number.isFinite(seconds) ? Math.min(seconds, 3600) : null;
}
