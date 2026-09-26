/**
 * マジックリンクの確認画面（components/auth/confirm-login-form.tsx）の「ログインする」を fetch で送り、
 * 応答の遷移先へ location.replace() で移る（確認画面の URL を履歴に残さない）。
 *
 * フォームの通常の送信（POST → 303）はナビゲーションとして履歴を 1 つ積むため、ログイン後に「戻る」と
 * 確認画面（/auth/confirm?token_hash=…。トークンは使用済み）へ戻ってしまう。fetch で送れば確認画面の
 * エントリを遷移先で置き換えられる。JS が無い・読み込み前の場合は通常のフォーム送信のまま（動作は同じ）。
 * 応答の形は lib/auth/route-helpers.ts の redirectTo（Accept: application/json のとき { location }）。
 */

/** 送信先（app/auth/confirm/verify/route.ts） */
export const CONFIRM_VERIFY_PATH = "/auth/confirm/verify";

export interface ConfirmFields {
  tokenHash: string;
  type: string;
  next: string;
}

/** 同一オリジンの相対パス（"/..."。"//host" などは不可）だけを遷移先として受け付ける */
export function isSameOriginPath(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.startsWith("/\\") &&
    !/[\u0000-\u001f]/.test(value)
  );
}

/** 応答から遷移先を取り出す。読めなければ null（呼び出し側でエラーを表示する） */
export async function confirmResponseLocation(
  response: Pick<Response, "ok" | "json">,
): Promise<string | null> {
  if (!response.ok) return null;
  const body: unknown = await response.json().catch(() => null);
  const location =
    typeof body === "object" && body !== null ? (body as { location?: unknown }).location : null;
  return isSameOriginPath(location) ? location : null;
}

/**
 * 「ログインする」を送信して、遷移先のパスを返す（Cookie は応答で発行される）。
 * 通信に失敗したら例外、応答が読めなければ null。
 */
export async function submitConfirm(
  fields: ConfirmFields,
  fetchImpl: typeof fetch = fetch,
): Promise<string | null> {
  const response = await fetchImpl(CONFIRM_VERIFY_PATH, {
    method: "POST",
    headers: { Accept: "application/json" },
    body: new URLSearchParams({
      token_hash: fields.tokenHash,
      type: fields.type,
      next: fields.next,
    }),
    credentials: "same-origin",
    cache: "no-store",
  });
  return confirmResponseLocation(response);
}
