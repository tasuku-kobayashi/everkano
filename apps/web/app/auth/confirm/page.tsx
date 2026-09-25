import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConfirmLoginForm } from "@/components/auth/confirm-login-form";
import { buttonClassName } from "@/components/ui/button";
import { Wordmark } from "@/components/ui/wordmark";
import {
  type ConfirmParamName,
  parseConfirmNext,
  parseConfirmParams,
} from "@/lib/auth/confirm-params";
import { loginPath } from "@/lib/auth/redirect";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "ログインの確認",
  // URL にトークンが含まれるため、どこへも Referer として送らない
  referrer: "no-referrer",
};

interface ConfirmPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

/**
 * マジックリンクの着地点（メールテンプレート infra/supabase/templates/magic_link.html のリンク先）。
 * GET /auth/confirm?token_hash=...&type=email&redirect_to=<emailRedirectTo>
 * ログイン後の遷移先は redirect_to（`<SITE_URL>/auth/callback?next=<ログイン前に開こうとしていたページ>`）の
 * next から取り出す（lib/auth/confirm-params.ts。直接の ?next= も受け付ける）。
 *
 * リンクを開いただけではログインしない。確認画面を表示し、「ログインする」ボタンで
 * POST /auth/confirm/verify へ送信したときに初めて token_hash を検証してセッションを発行する
 * （メールスキャナーの先読みでトークンが消費される問題と、ログイン CSRF の対策）。
 * 既に別のアカウントでログインしている場合は、切り替わることを明示する。
 */
export default async function ConfirmPage({ searchParams }: ConfirmPageProps) {
  const query = await searchParams;
  const get = (name: ConfirmParamName) => first(query[name]);
  const params = parseConfirmParams(get);
  if (!params) {
    console.warn("[auth/confirm] missing or invalid token_hash/type");
    redirect(loginPath({ error: "link", next: parseConfirmNext(get) }));
  }

  const supabase = await createSupabaseServerClient();
  const { data } = await supabase.auth.getClaims();
  const claims = data?.claims;
  const currentEmail =
    claims && typeof claims.sub === "string"
      ? typeof claims.email === "string" && claims.email
        ? claims.email
        : "別のアカウント"
      : null;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col px-8 pt-safe pb-safe">
      <div className="flex flex-1 flex-col items-stretch justify-center py-10 text-center">
        <div className="mb-6 flex justify-center">
          <Wordmark height={48} />
        </div>
        <h1 className="text-[18px] leading-6 font-semibold">everkano にログインしますか？</h1>
        <p className="mt-2 text-[14px] leading-[18px] text-ig-secondary">
          下のボタンを押すと、メールに届いたリンクでログインします。
        </p>

        {currentEmail ? (
          <p
            role="note"
            data-testid="confirm-account-switch"
            className="mt-5 rounded-xl bg-ig-elevated px-4 py-3 text-left text-[14px] leading-[18px]"
          >
            現在 <span className="font-semibold break-all">{currentEmail}</span>{" "}
            でログインしています。続けると、このリンクのアカウントに切り替わります。
          </p>
        ) : null}

        <ConfirmLoginForm tokenHash={params.tokenHash} type={params.type} next={params.next} />

        <Link
          href={currentEmail ? "/" : "/login"}
          className={buttonClassName({ variant: "ghost", size: "lg", fullWidth: true })}
        >
          {currentEmail ? "ログインせずにホームへ" : "キャンセル"}
        </Link>

        <p className="mt-8 text-[12px] leading-4 text-ig-secondary">
          このリンクを自分でリクエストしていない場合は、ログインせずにこの画面を閉じてください。
          everkano がログインリンクや確認コードを尋ねることはありません。
        </p>
      </div>
    </main>
  );
}
