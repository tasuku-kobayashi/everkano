"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useScrollTopOrRefreshFeed } from "@/lib/feed-refresh";
import { useBackNavigation } from "@/lib/navigation";
import { ChevronLeftIcon } from "./icons";
import { Wordmark } from "./wordmark";

interface HeaderCommonProps {
  /** 右側のアクション（アイコンボタン等）。<HeaderIconButton> を使うと寸法が揃う */
  right?: ReactNode;
  /** 下に区切り線を引く（投稿詳細・DM など） */
  bordered?: boolean;
  className?: string;
}

export interface LogoHeaderProps extends HeaderCommonProps {
  variant: "logo";
  /** 画面の見出し（視覚的には非表示の h1。スクリーンリーダーの見出し移動用）。既定「ホーム」 */
  heading?: string;
}

export interface BackHeaderProps extends HeaderCommonProps {
  variant: "back";
  /** 見出し（center / left 揃え） */
  title?: ReactNode;
  /** 見出しの下の小さな補足（例: DM の「アクティブ」） */
  subtitle?: ReactNode;
  /** title の代わりに任意の中身（DM ヘッダーのアバター + 名前など）。左揃えで表示 */
  children?: ReactNode;
  /**
   * children を使う場合の画面の見出し（視覚的には非表示の h1。例: DM 会話ならキャラクター名）。
   * title を指定した場合は title が h1 になるため不要
   */
  heading?: string;
  /** 既定 "center"。children 指定時は常に left */
  align?: "center" | "left";
  /** 戻り先（アプリ内履歴が無い場合）。既定 "/" */
  backHref?: string;
  /** 戻る処理を上書き */
  onBack?: () => void;
}

export interface TitleHeaderProps extends HeaderCommonProps {
  variant: "title";
  /** 左寄せの大きな見出し（Instagram の自分プロフィール / DM 一覧の形式） */
  title: ReactNode;
  /** 見出しの左に置く要素 */
  left?: ReactNode;
}

export type AppHeaderProps = LogoHeaderProps | BackHeaderProps | TitleHeaderProps;

/**
 * 画面上部の固定ヘッダー（sticky・safe-area 対応・高さ 44px）。
 *
 * - variant="logo" : ワードマーク + 右アクション（ホーム）
 * - variant="back" : 「<」戻る + 見出し（投稿詳細・キャラプロフィール・DM 会話）
 * - variant="title": 左寄せの太字見出し（DM 一覧・自分のプロフィール・検索）
 *
 * どの形式でも画面の h1 を 1 つ出す（logo は非表示の「ホーム」、back + children は heading を非表示で）。
 */
export function AppHeader(props: AppHeaderProps) {
  return (
    <header
      className={cn(
        "sticky top-0 z-30 bg-ig-bg/95 pt-safe backdrop-blur-md supports-[backdrop-filter]:bg-ig-bg/85",
        props.bordered && "border-b border-ig-separator",
        props.className,
      )}
    >
      <div className="relative flex h-[var(--header-h)] items-center gap-2 px-3">
        {props.variant === "logo" ? <LogoContent heading={props.heading} /> : null}
        {props.variant === "back" ? <BackContent {...props} /> : null}
        {props.variant === "title" ? <TitleContent {...props} /> : null}
        {props.right ? (
          <div className="ml-auto flex shrink-0 items-center gap-1">{props.right}</div>
        ) : null}
      </div>
    </header>
  );
}

function LogoContent({ heading = "ホーム" }: { heading?: string }) {
  const pathname = usePathname();
  const scrollTopOrRefreshFeed = useScrollTopOrRefreshFeed();
  return (
    <>
      <h1 className="sr-only">{heading}</h1>
      <Link
        href="/"
        aria-label="everkano ホーム"
        onClick={(event) => {
          // ホーム表示中のロゴタップ: 先頭へ戻る / 先頭なら再読み込み（Home タブの再タップと同じ）
          if (pathname !== "/") return;
          event.preventDefault();
          scrollTopOrRefreshFeed();
        }}
        className="-mb-1 flex items-center pl-1 pressable"
      >
        <Wordmark height={30} />
      </Link>
    </>
  );
}

function BackButton({ backHref, onBack }: Pick<BackHeaderProps, "backHref" | "onBack">) {
  const goBack = useBackNavigation(backHref ?? "/");
  return (
    <button
      type="button"
      onClick={onBack ?? goBack}
      aria-label="戻る"
      className="-ml-1 flex size-10 shrink-0 items-center justify-center pressable"
    >
      <ChevronLeftIcon size={26} strokeWidth={2.2} />
    </button>
  );
}

function BackContent({
  title,
  subtitle,
  children,
  heading,
  align = "center",
  backHref,
  onBack,
}: BackHeaderProps) {
  const centered = !children && align === "center";
  return (
    <>
      <BackButton backHref={backHref} onBack={onBack} />
      {children ? (
        <div className="flex min-w-0 flex-1 items-center">
          {heading ? <h1 className="sr-only">{heading}</h1> : null}
          {children}
        </div>
      ) : (
        <div
          className={cn(
            "flex min-w-0 flex-col",
            centered
              ? "pointer-events-none absolute inset-x-14 items-center text-center"
              : "flex-1 items-start",
          )}
        >
          {title ? (
            <h1 className="max-w-full truncate text-[16px] leading-5 font-bold">{title}</h1>
          ) : null}
          {subtitle ? (
            <p className="max-w-full truncate text-[12px] leading-4 text-ig-secondary">
              {subtitle}
            </p>
          ) : null}
        </div>
      )}
    </>
  );
}

function TitleContent({ title, left }: TitleHeaderProps) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2 pl-1">
      {left}
      <h1 className="truncate text-[22px] leading-7 font-bold tracking-tight">{title}</h1>
    </div>
  );
}

export interface HeaderIconButtonProps {
  /** アクセシブルな名前（必須） */
  label: string;
  children: ReactNode;
  onClick?: () => void;
  /** 指定するとリンクになる */
  href?: string;
  className?: string;
}

/** ヘッダー右側のアイコンボタン（44x44 のタップ領域） */
export function HeaderIconButton({
  label,
  children,
  onClick,
  href,
  className,
}: HeaderIconButtonProps) {
  const classes = cn("pressable flex size-10 items-center justify-center", className);
  if (href) {
    return (
      <Link href={href} aria-label={label} className={classes}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" aria-label={label} onClick={onClick} className={classes}>
      {children}
    </button>
  );
}
