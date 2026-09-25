/**
 * フォームの POST が同じオリジンのページから送られたか（クロスサイトからのログイン CSRF を防ぐ）。
 *
 * - Sec-Fetch-Site があればそれで判定する（現行のブラウザはすべて送る。same-site のサブドメインも拒否）
 * - 無い古いブラウザでは Origin ヘッダーを自分のオリジンと比較する
 * - どちらも無い（ブラウザ以外からのリクエスト等）は拒否する
 */
export function isSameOriginRequest(headers: Headers, origin: string): boolean {
  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite) return fetchSite === "same-origin";
  const requestOrigin = headers.get("origin");
  return requestOrigin !== null && requestOrigin !== "null" && requestOrigin === origin;
}
