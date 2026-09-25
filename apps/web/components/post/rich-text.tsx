import { Fragment, useMemo } from "react";
import { tokenizeRichText } from "./rich-text-tokens";

/**
 * 本文の表示（ハッシュタグ・@メンションを Instagram と同じリンク色で表示する。リンクにはしない）。
 * 該当が無ければ文字列をそのまま返す（余計な要素を増やさない）。
 */
export function RichText({ text }: { text: string }) {
  const tokens = useMemo(() => tokenizeRichText(text), [text]);
  if (tokens.every((token) => token.type === "text")) return <>{text}</>;
  return (
    <>
      {tokens.map((token, index) =>
        token.type === "text" ? (
          <Fragment key={index}>{token.value}</Fragment>
        ) : (
          <span key={index} className="text-ig-link" data-rich-text={token.type}>
            {token.value}
          </span>
        ),
      )}
    </>
  );
}
