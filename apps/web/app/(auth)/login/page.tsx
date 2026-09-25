import type { Metadata } from "next";
import { LoginForm } from "@/components/auth/login-form";
import { parseLoginError, sanitizeNextPath } from "@/lib/auth/redirect";

export const metadata: Metadata = { title: "ログイン" };

interface LoginPageProps {
  searchParams: Promise<{ error?: string | string[]; next?: string | string[] }>;
}

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  return (
    <LoginForm
      initialError={parseLoginError(first(params.error))}
      nextPath={sanitizeNextPath(first(params.next))}
    />
  );
}
