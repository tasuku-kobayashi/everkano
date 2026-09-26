/** 相談窓口のカード（safety-resource-card.tsx）のリンクの組み立て。 */

/** 電話番号 → tel: の URL（数字と先頭の + だけ。番号として短すぎれば null） */
export function telHref(phone: string): string | null {
  const trimmed = phone.trim();
  const digits = trimmed.replace(/[^\d]/g, "");
  if (digits.length < 3) return null;
  return `tel:${trimmed.startsWith("+") ? "+" : ""}${digits}`;
}

/** 外部リンクにしてよい URL（http / https だけ。それ以外・解釈できない値は null） */
export function safeExternalUrl(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}
