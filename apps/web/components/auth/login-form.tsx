"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { MailIcon } from "@/components/ui/icons";
import { Wordmark } from "@/components/ui/wordmark";
import { authErrorMessage, isValidEmail } from "@/lib/auth/errors";
import { LOGIN_ERROR_MESSAGES, type LoginErrorReason } from "@/lib/auth/redirect";
import { isProfileWithdrawn } from "@/lib/auth/withdrawn";
import { cn } from "@/lib/cn";
import { getPublicEnv } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** 再送信までの待ち時間（秒）。Supabase の既定のメール送信間隔（60 秒）に合わせる */
const RESEND_COOLDOWN_SECONDS = 60;
const OTP_LENGTH = 6;

export interface LoginFormProps {
  initialError: LoginErrorReason | null;
  /** ログイン後の遷移先（検証済みの相対パス） */
  nextPath: string;
}

type Step = "email" | "code";

/**
 * ログイン画面（仕様 §5.1）。
 * 1. メールアドレス → signInWithOtp でマジックリンク（+ 6 桁コード）を送信
 * 2. 「メールを確認してください」: メールのリンク（/auth/confirm）か、6 桁コードを入力して verifyOtp
 *    ※ iOS のホーム画面 PWA は Safari と Cookie を共有しないため、PWA からはコード入力でログインする
 */
export function LoginForm({ initialError, nextPath }: LoginFormProps) {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(
    initialError ? LOGIN_ERROR_MESSAGES[initialError] : null,
  );
  const [cooldown, setCooldown] = useState(0);
  const codeInputRef = useRef<HTMLInputElement>(null);
  const autoSubmittedRef = useRef<string | null>(null);

  // 退会済みで戻ってきた場合、念のためこの端末のセッションを破棄する
  useEffect(() => {
    if (initialError === "withdrawn") {
      void getSupabaseBrowserClient()
        .auth.signOut({ scope: "local" })
        .then(({ error: signOutError }) => {
          if (signOutError) console.warn("[login] sign out failed:", signOutError.message);
        });
    }
  }, [initialError]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  useEffect(() => {
    if (step === "code") codeInputRef.current?.focus();
  }, [step]);

  async function sendLink(targetEmail: string): Promise<boolean> {
    setSending(true);
    setError(null);
    try {
      const siteUrl = getPublicEnv().siteUrl ?? window.location.origin;
      const { error: otpError } = await getSupabaseBrowserClient().auth.signInWithOtp({
        email: targetEmail,
        options: {
          emailRedirectTo: `${siteUrl}/auth/callback`,
          shouldCreateUser: true,
        },
      });
      if (otpError) {
        console.warn("[login] signInWithOtp failed:", otpError.code, otpError.message);
        setError(authErrorMessage(otpError, "send"));
        return false;
      }
      setCooldown(RESEND_COOLDOWN_SECONDS);
      return true;
    } catch (cause) {
      console.error("[login] signInWithOtp threw:", cause);
      setError("通信できませんでした。電波の良い場所で再度お試しください");
      return false;
    } finally {
      setSending(false);
    }
  }

  async function onSubmitEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!isValidEmail(trimmed)) {
      setError("メールアドレスの形式が正しくありません");
      return;
    }
    if (await sendLink(trimmed)) {
      setEmail(trimmed);
      setCode("");
      autoSubmittedRef.current = null;
      setStep("code");
    }
  }

  async function verify(token: string) {
    if (verifying) return;
    setVerifying(true);
    setError(null);
    try {
      const supabase = getSupabaseBrowserClient();
      const { data, error: verifyError } = await supabase.auth.verifyOtp({
        email,
        token,
        type: "email",
      });
      if (verifyError || !data.user) {
        console.warn("[login] verifyOtp failed:", verifyError?.code, verifyError?.message);
        setError(
          verifyError ? authErrorMessage(verifyError, "verify") : "ログインできませんでした",
        );
        return;
      }
      if (await isProfileWithdrawn(supabase, data.user.id)) {
        await supabase.auth.signOut({ scope: "local" });
        setError(LOGIN_ERROR_MESSAGES.withdrawn);
        setStep("email");
        return;
      }
      router.replace(nextPath);
      router.refresh();
    } catch (cause) {
      console.error("[login] verifyOtp threw:", cause);
      setError("通信できませんでした。電波の良い場所で再度お試しください");
    } finally {
      setVerifying(false);
    }
  }

  function onSubmitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code.length === OTP_LENGTH) void verify(code);
  }

  function onChangeCode(value: string) {
    const digits = value.replace(/\D/g, "").slice(0, OTP_LENGTH);
    setCode(digits);
    // 6 桁そろったら自動でログイン（同じコードでは 1 回だけ）
    if (digits.length === OTP_LENGTH && autoSubmittedRef.current !== digits) {
      autoSubmittedRef.current = digits;
      void verify(digits);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col px-8 pt-safe pb-safe">
      <div className="flex flex-1 flex-col justify-center py-10">
        {step === "email" ? (
          <form onSubmit={onSubmitEmail} noValidate className="flex flex-col items-stretch">
            <div className="mb-3 flex justify-center">
              <Wordmark height={56} />
            </div>
            <p className="mb-8 text-center text-[15px] leading-5 font-semibold text-ig-secondary">
              AIキャラクターたちの毎日をのぞいて、
              <br />
              DMで話そう。
            </p>

            <label htmlFor="login-email" className="sr-only">
              メールアドレス
            </label>
            <input
              id="login-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder="メールアドレス"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : undefined}
              className="h-12 w-full rounded-xl border border-ig-input-border bg-ig-input-bg px-4 text-[16px] text-ig-text outline-none placeholder:text-ig-secondary focus:border-ig-secondary"
            />

            <Button
              type="submit"
              size="lg"
              fullWidth
              className="mt-3"
              loading={sending}
              disabled={email.trim().length === 0}
            >
              ログインリンクを送信
            </Button>

            <ErrorText message={error} />

            <p className="mt-8 text-center text-[12px] leading-4 text-ig-secondary">
              アカウントをお持ちでない場合も、メールアドレスを入力するだけで始められます。
            </p>
          </form>
        ) : (
          <form
            onSubmit={onSubmitCode}
            noValidate
            className="flex flex-col items-center text-center"
          >
            <div className="mb-4 flex size-[88px] items-center justify-center rounded-full border-2 border-ig-text">
              <MailIcon size={44} strokeWidth={1.4} />
            </div>
            <h1 className="text-[18px] leading-6 font-semibold">メールを確認してください</h1>
            <p className="mt-2 text-[14px] leading-[18px] text-ig-secondary">
              <span className="font-semibold break-all text-ig-text">{email}</span>{" "}
              にログインリンクを送信しました。メール内のリンクをタップするか、記載されている6桁の確認コードを入力してください。
            </p>

            <label htmlFor="login-code" className="sr-only">
              確認コード（6桁）
            </label>
            <input
              ref={codeInputRef}
              id="login-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]*"
              maxLength={OTP_LENGTH}
              placeholder="確認コード"
              value={code}
              onChange={(event) => onChangeCode(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : undefined}
              className={cn(
                "mt-6 h-14 w-full rounded-xl border border-ig-input-border bg-ig-input-bg px-4 text-center text-[24px] font-semibold tracking-[0.5em] text-ig-text outline-none focus:border-ig-secondary",
                "placeholder:text-[16px] placeholder:font-normal placeholder:tracking-normal placeholder:text-ig-secondary",
              )}
            />

            <Button
              type="submit"
              size="lg"
              fullWidth
              className="mt-3"
              loading={verifying}
              disabled={code.length !== OTP_LENGTH}
            >
              コードでログイン
            </Button>

            <ErrorText message={error} />

            <div className="mt-6 flex flex-col items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                disabled={cooldown > 0 || sending}
                loading={sending}
                onClick={() => {
                  autoSubmittedRef.current = null;
                  void sendLink(email);
                }}
              >
                {cooldown > 0 ? `コードを再送信（${cooldown}秒）` : "コードを再送信"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-ig-text"
                onClick={() => {
                  setStep("email");
                  setError(null);
                  setCode("");
                }}
              >
                メールアドレスを変更
              </Button>
            </div>

            <p className="mt-6 text-[12px] leading-4 text-ig-secondary">
              ホーム画面に追加したアプリをお使いの場合は、確認コードでログインしてください。
            </p>
          </form>
        )}
      </div>
    </main>
  );
}

function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      id="login-error"
      role="alert"
      className="mt-4 text-center text-[14px] leading-[18px] text-ig-red"
    >
      {message}
    </p>
  );
}
