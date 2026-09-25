"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

/**
 * マジックリンクの確認画面（app/auth/confirm/page.tsx）の「ログインする」フォーム。
 * JS なしでも送信できる通常のフォーム（POST /auth/confirm/verify → 303 で next へ）。
 * 二重送信するとトークンが 2 回使われて 2 回目が失敗するため、送信後はボタンを無効にする。
 */
export function ConfirmLoginForm({
  tokenHash,
  type,
  next,
}: {
  tokenHash: string;
  type: string;
  next: string;
}) {
  const [submitting, setSubmitting] = useState(false);

  // 「戻る」でこの画面が bfcache から復元された場合はボタンを押せる状態に戻す
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setSubmitting(false);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  return (
    <form
      method="post"
      action="/auth/confirm/verify"
      className="mt-6 mb-2"
      onSubmit={(event) => {
        if (submitting) {
          event.preventDefault();
          return;
        }
        setSubmitting(true);
      }}
    >
      <input type="hidden" name="token_hash" value={tokenHash} />
      <input type="hidden" name="type" value={type} />
      <input type="hidden" name="next" value={next} />
      <Button type="submit" size="lg" fullWidth loading={submitting}>
        ログインする
      </Button>
    </form>
  );
}
