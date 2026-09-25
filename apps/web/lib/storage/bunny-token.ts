import "server-only";
import { createHash } from "node:crypto";

/**
 * ============================================================================
 *  サーバー専用: Bunny.net Token Authentication の署名URL生成
 *  （BUNNY_TOKEN_AUTH_KEY を扱うため、クライアントから import してはいけない）
 * ============================================================================
 *
 * Bunny.net の Token Authentication（SHA256 方式）に準拠:
 *   hashable = security_key + signed_path + expires [+ user_ip] [+ "k1=v1&k2=v2"(キー昇順・未エンコード)]
 *   token    = Base64(SHA256_raw(hashable)) を URL-safe 化（'+'→'-', '/'→'_', '=' と改行を除去）
 *   URL      = https://<zone>/<path>?token=<token>&<params...>&expires=<unix秒>
 * 参考: https://docs.bunny.net/docs/cdn-token-authentication （公式の PHP / Node サンプルと同じ手順）
 *
 * Bunny Optimizer の変換パラメータ（width 等）も署名対象に含める（Bunny は token/expires 以外の
 * クエリをすべて検証するため）。
 */

export interface SignBunnyUrlInput {
  /** Pull Zone のURL（例: https://everkano.b-cdn.net） */
  cdnBaseUrl: string;
  /** URL エンコード済みのパス（先頭 / 付き）。例: /posts/a.jpg */
  path: string;
  /** Pull Zone の Token Authentication Key */
  securityKey: string;
  /** 失効時刻（UNIX 秒） */
  expires: number;
  /** 署名に含めるクエリパラメータ（width / quality / blur など） */
  params?: Array<[string, string]>;
  /** IP 制限を使う場合のみ */
  userIp?: string;
}

/** Base64 → URL-safe（Bunny の仕様どおり） */
export function toBunnyBase64Url(buffer: Buffer): string {
  return buffer
    .toString("base64")
    .replace(/\n/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

export function computeBunnyToken(input: Omit<SignBunnyUrlInput, "cdnBaseUrl">): string {
  const params = [...(input.params ?? [])].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const parameterData = params.map(([k, v]) => `${k}=${v}`).join("&");
  const hashable = `${input.securityKey}${input.path}${input.expires}${input.userIp ?? ""}${parameterData}`;
  return toBunnyBase64Url(createHash("sha256").update(hashable, "utf8").digest());
}

/** トークン署名済みの Bunny CDN URL を返す */
export function signBunnyUrl(input: SignBunnyUrlInput): string {
  const token = computeBunnyToken(input);
  const params = [...(input.params ?? [])].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const paramQuery = params.map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join("");
  const base = input.cdnBaseUrl.replace(/\/+$/, "");
  return `${base}${input.path}?token=${token}${paramQuery}&expires=${input.expires}`;
}

/**
 * 失効時刻を計算する。同じ画像のURLがしばらく同一になるよう 5 分単位に切り上げる
 * （ブラウザ / CDN キャッシュのヒット率を上げるため）。最低でも ttlSeconds は有効。
 */
export function computeExpires(nowMs: number, ttlSeconds: number, bucketSeconds = 300): number {
  const minimum = Math.floor(nowMs / 1000) + ttlSeconds;
  return Math.ceil(minimum / bucketSeconds) * bucketSeconds;
}
