/**
 * 共通の型ガード。複数のモジュール（API の応答の正規化・SSE・履歴の state・URL の id の検証）で
 * 同じ判定を重複して持たないようにまとめる。
 */

/** プレーンなオブジェクト（null・配列を除く）か。unknown の値（API の応答・history.state など）の絞り込み用 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * UUID 形式か。URL の id（postId / characterId）を問い合わせる前に弾く
 * （不正な値で DB に問い合わせると 22P02 エラーになり、API では「見つかりません」を出す）。
 */
export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}
