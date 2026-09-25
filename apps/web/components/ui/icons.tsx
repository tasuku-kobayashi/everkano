import { useId, type ReactNode, type SVGProps } from "react";

/**
 * Instagram 風のアウトラインアイコン（手作りの SVG。アイコンライブラリは使わない）。
 * - 24x24 グリッド・線幅 2・角丸。色は currentColor（親の text-* で指定）。
 * - title を渡すとスクリーンリーダー向けのラベルになる。無ければ aria-hidden。
 */

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  /** 表示サイズ（px）。既定 24 */
  size?: number;
  /** アクセシブルな名前。装飾目的なら省略 */
  title?: string;
}

function Svg({
  size = 24,
  title,
  strokeWidth = 2,
  children,
  ...rest
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

const HOME_PATH = "M3 10.4 12 3.2l9 7.2V21h-5.6v-5.2a3.4 3.4 0 0 0-6.8 0V21H3Z";
// 塗りつぶし版はドアの切り欠きが線幅で埋もれないよう少し広くする
const HOME_FILLED_PATH = "M3 10.4 12 3.2l9 7.2V21h-5.1v-4.6a3.9 3.9 0 0 0-7.8 0V21H3Z";

export function HomeIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={HOME_PATH} />
    </Svg>
  );
}

export function HomeFilledIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={HOME_FILLED_PATH} fill="currentColor" />
    </Svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="10.5" cy="10.5" r="7" />
      <path d="m16.2 16.2 4.8 4.8" />
    </Svg>
  );
}

/** 検索タブのアクティブ状態（太線） */
export function SearchBoldIcon(props: IconProps) {
  return (
    <Svg strokeWidth={3} {...props}>
      <circle cx="10.5" cy="10.5" r="6.8" />
      <path d="m16 16 4.8 4.8" />
    </Svg>
  );
}

const PLANE_OUTLINE = "M21.5 2.5 2.6 9.4l7.7 4.3 4.4 7.8Z";
const PLANE_FOLD = "M21.5 2.5 10.3 13.7";

export function PaperPlaneIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={PLANE_OUTLINE} />
      <path d={PLANE_FOLD} />
    </Svg>
  );
}

/** DM タブのアクティブ状態（塗りつぶし。折り目は背景色で抜く） */
export function PaperPlaneFilledIcon(props: IconProps) {
  const maskId = useId();
  return (
    <Svg {...props}>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="white" stroke="none" />
        <path d={PLANE_FOLD} stroke="black" strokeWidth={1.6} />
      </mask>
      <path d={PLANE_OUTLINE} fill="currentColor" mask={`url(#${maskId})`} />
    </Svg>
  );
}

/** 投稿のシェア（Instagram と同じく紙飛行機） */
export function ShareIcon(props: IconProps) {
  return <PaperPlaneIcon {...props} />;
}

const HEART_PATH =
  "M12 20.6c-.3 0-.6-.1-.8-.3C7.3 17 2.6 13 2.6 8.6c0-3 2.3-5.3 5.1-5.3 1.8 0 3.3.9 4.3 2.4 1-1.5 2.5-2.4 4.3-2.4 2.8 0 5.1 2.3 5.1 5.3 0 4.4-4.7 8.4-8.6 11.7-.2.2-.5.3-.8.3Z";

export function HeartIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={HEART_PATH} />
    </Svg>
  );
}

/** いいね済み（色は呼び出し側で text-ig-red を指定） */
export function HeartFilledIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={HEART_PATH} fill="currentColor" />
    </Svg>
  );
}

export function CommentIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20.4 16.4A9.1 9.1 0 1 0 16.9 20l4.6 1.5Z" />
    </Svg>
  );
}

const BOOKMARK_PATH = "M19 21 12 14.8 5 21V3.5h14Z";

export function BookmarkIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={BOOKMARK_PATH} />
    </Svg>
  );
}

export function BookmarkFilledIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d={BOOKMARK_PATH} fill="currentColor" />
    </Svg>
  );
}

/** 「…」 */
export function MoreIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="5.5" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="18.5" cy="12" r="1.5" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function LockIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="4.5" y="10.5" width="15" height="11" rx="2.5" />
      <path d="M8 10.5V7.4a4 4 0 0 1 8 0v3.1" />
      <path d="M12 14.6v2.6" />
    </Svg>
  );
}

/** 塗りつぶしの鍵（有料バッジ・サムネイル上など小さく表示する用途） */
export function LockFilledIcon(props: IconProps) {
  const maskId = useId();
  return (
    <Svg {...props}>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="white" stroke="none" />
        <path d="M12 14.4v2.8" stroke="black" strokeWidth={2.2} />
      </mask>
      <path d="M8 10.5V7.4a4 4 0 0 1 8 0v3.1" />
      <rect
        x="4.5"
        y="10.5"
        width="15"
        height="11"
        rx="2.5"
        fill="currentColor"
        mask={`url(#${maskId})`}
      />
    </Svg>
  );
}

export function ChevronLeftIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M15.5 4.5 8 12l7.5 7.5" />
    </Svg>
  );
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8.5 4.5 16 12l-7.5 7.5" />
    </Svg>
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4.5 8.5 12 16l7.5-7.5" />
    </Svg>
  );
}

/** 「i」（DM ヘッダー右のメモリパネル） */
export function InfoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M12 11v6" />
      <circle cx="12" cy="7.6" r="1.25" fill="currentColor" stroke="none" />
    </Svg>
  );
}

/** プロフィールの投稿グリッドタブ */
export function GridIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="3" width="18" height="18" rx="1.5" />
      <path d="M9 3v18M15 3v18M3 9h18M3 15h18" />
    </Svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.5 5.5 18.5 18.5M18.5 5.5 5.5 18.5" />
    </Svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 4.5v15M4.5 12h15" />
    </Svg>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 6.5h16M9.5 6.5V4.2h5v2.3M6.3 6.5l1 13.8h9.4l1-13.8M10 10.5v6M14 10.5v6" />
    </Svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m4.8 12.6 4.6 4.6 9.8-10" />
    </Svg>
  );
}

/** 「≡」（設定メニュー） */
export function MenuIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 6h17M3.5 12h17M3.5 18h17" />
    </Svg>
  );
}

export function MailIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2.8" y="5" width="18.4" height="14" rx="2.2" />
      <path d="m3.4 6.4 8.6 6.6 8.6-6.6" />
    </Svg>
  );
}

/** 警告（エラー表示） */
export function AlertIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M12 7v6.2" />
      <circle cx="12" cy="16.6" r="1.25" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function WifiOffIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.5 8.8a14.5 14.5 0 0 1 4-2.6M10.4 5.2a14.6 14.6 0 0 1 11.1 3.6" />
      <path d="M5.6 12.2a10 10 0 0 1 4.1-2.3M14.6 10a10 10 0 0 1 3.8 2.2" />
      <path d="M9 15.6a5 5 0 0 1 6 0" />
      <circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none" />
      <path d="m3.5 3.5 17 17" />
    </Svg>
  );
}

/** 人物シルエット（アバター画像が無い場合） */
export function UserIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="8.2" r="4.2" />
      <path d="M4 20.5c.9-3.9 4.2-6.2 8-6.2s7.1 2.3 8 6.2" />
    </Svg>
  );
}

/** 画像（読み込み失敗時のプレースホルダー） */
export function ImageIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <circle cx="8.8" cy="8.8" r="1.8" />
      <path d="m21 15.2-4.6-4.6L6 21" />
    </Svg>
  );
}

/** ログアウト */
export function LogOutIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 4.5H6.5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2H14" />
      <path d="M10 12h10.5M17 8.5l3.5 3.5-3.5 3.5" />
    </Svg>
  );
}
