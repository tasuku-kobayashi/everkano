import { describe, expect, it } from "vitest";
import { tokenizeRichText, type RichTextToken } from "./rich-text-tokens";

const highlighted = (tokens: RichTextToken[]) =>
  tokens.filter((token) => token.type !== "text").map((token) => `${token.type}:${token.value}`);

describe("tokenizeRichText", () => {
  it("ハッシュタグ（日本語・全角＃・長音符）を切り出す", () => {
    expect(
      highlighted(tokenizeRichText("夕焼けがきれい #虹 #夕焼け\n＃ネイルデザイン #ラーメン🍜")),
    ).toEqual(["hashtag:#虹", "hashtag:#夕焼け", "hashtag:＃ネイルデザイン", "hashtag:#ラーメン"]);
  });

  it("@メンション（handle の形式。末尾の句点は含めない）を切り出す", () => {
    expect(highlighted(tokenizeRichText("@kaede.tonari ありがとう！ @user_0b0aff."))).toEqual([
      "mention:@kaede.tonari",
      "mention:@user_0b0aff",
    ]);
  });

  it("連結すると元の文字列に戻る", () => {
    const text = "🌸はじめて #食券機 を使いました @misaki_ol さん、C# と a@example.com は対象外";
    expect(
      tokenizeRichText(text)
        .map((token) => token.value)
        .join(""),
    ).toBe(text);
  });

  it("語中の #・メールアドレス・数字だけのタグは対象外", () => {
    expect(
      highlighted(tokenizeRichText("C#で書いた / 今日の#ネイル / #1 / a@example.com")),
    ).toEqual([]);
  });

  it("絵文字の直後のハッシュタグは対象", () => {
    expect(highlighted(tokenizeRichText("✨#映え"))).toEqual(["hashtag:#映え"]);
  });

  it("該当が無ければテキスト 1 つ（空文字は空配列）", () => {
    expect(tokenizeRichText("今日もおつかれさま。")).toEqual([
      { type: "text", value: "今日もおつかれさま。" },
    ]);
    expect(tokenizeRichText("")).toEqual([]);
  });
});
