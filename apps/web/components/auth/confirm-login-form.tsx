"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { API_ERROR_MESSAGES } from "@/lib/api/errors";
import { CONFIRM_VERIFY_PATH, submitConfirm } from "@/lib/auth/confirm-submit";

/** 応答が読めなかったとき（サーバーの障害など） */
const CONFIRM_FAILED_MESSAGE = "ログインできませんでした。しばらくしてから再度お試しください。";

/**
 * マジックリンクの確認画面（app/auth/confirm/page.tsx）の「ログインする」フォーム。
 *
 * JS が動いていれば fetch で送信し（POST /auth/confirm/verify・Accept: application/json）、応答の遷移先へ
 * location.replace() で移る。確認画面（/auth/confirm?token_hash=…）の履歴エントリを遷移先で置き換えるので、
 * ログイン後の「戻る」で使用済みのリンクの確認画面へ戻らない（lib/auth/confirm-submit.ts）。
 * JS が無い・読み込み前の場合は通常のフォーム送信（POST → 303 で next へ）のまま動く。
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
  const [error, setError] = useState<string | null>(null);

  // 「戻る」でこの画面が bfcache から復元された場合（JS が無い状態で送信した後など）はボタンを押せる状態に戻す
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setSubmitting(false);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    submitConfirm({ tokenHash, type, next }).then(
      (location) => {
        if (location) {
          // 成功・失敗（使用済みのリンク → /login?error=link）とも、確認画面を履歴に残さずに移る
          window.location.replace(location);
          return;
        }
        setError(CONFIRM_FAILED_MESSAGE);
        setSubmitting(false);
      },
      () => {
        // 通信の失敗（サーバーに届いていない可能性が高い）: そのまま再試行できる
        setError(API_ERROR_MESSAGES.network_error);
        setSubmitting(false);
      },
    );
  };

  return (
    <form method="post" action={CONFIRM_VERIFY_PATH} className="mt-6 mb-2" onSubmit={onSubmit}>
      <input type="hidden" name="token_hash" value={tokenHash} />
      <input type="hidden" name="type" value={type} />
      <input type="hidden" name="next" value={next} />
      <Button type="submit" size="lg" fullWidth loading={submitting}>
        ログインする
      </Button>
      {error ? (
        <p role="alert" className="mt-3 text-center text-[14px] leading-[18px] text-ig-red-text">
          {error}
        </p>
      ) : null}
    </form>
  );
}
