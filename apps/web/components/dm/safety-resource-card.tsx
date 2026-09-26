import type { SafetyResource } from "@everkano/shared";
import { useId } from "react";
import { ExternalLinkIcon, HeartIcon, PhoneIcon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { safeExternalUrl, telHref } from "./safety-links";

export interface SafetyResourceCardProps {
  /** 相談窓口の一覧（GET /safety/resources。返答を受け取った端末では返答に入っていたもの）。未取得なら undefined */
  resources: readonly SafetyResource[] | undefined;
  /** 一覧を読み込み中 */
  loading?: boolean;
  /** 一覧を読み込めなかった（再読み込みのボタンを出す。119 番の案内と返答の本文の窓口は出たまま） */
  error?: boolean;
  onRetry?: () => void;
}

/**
 * E6: 相談窓口のカード（自傷・希死念慮のシグナルを検知して安全対応をしたキャラの返答の下に出す）。
 *
 * - どの返答の下に出すかはサーバーの印（messages.safety_triggered）で決まる（履歴・別の端末でも出る）
 * - うっかり閉じて見失わないよう、閉じる操作は置かない（会話を開くたびに同じ返答の下に出る）
 * - キャラの発言ではなくアプリからの案内なので、吹き出しとは別の見た目（横幅いっぱいの枠）にする
 * - 電話番号は tel: のリンク（タップで発信画面）。受付時間と、ウェブサイト（別のタブ）を添える
 * - 命に関わるときの 119 番を最後に案内する
 * - 見出し付きの region（スクリーンリーダーの見出し・ランドマーク移動で辿れる）。会話ログ（role="log"）の中に
 *   あるので、届いたときに読み上げられる
 */
export function SafetyResourceCard({
  resources,
  loading = false,
  error = false,
  onRetry,
}: SafetyResourceCardProps) {
  const headingId = useId();
  return (
    <section
      role="region"
      aria-labelledby={headingId}
      // キャラの吹き出しではなくアプリからの案内なので、吹き出しの字下げに合わせず横幅いっぱいに出す
      className="mx-3 mt-2 animate-fade-in rounded-2xl border border-ig-separator bg-ig-bg px-4 pt-3.5 pb-3"
      data-testid="safety-card"
    >
      <h3 id={headingId} className="flex items-center gap-1.5 text-[15px] leading-5 font-bold">
        <HeartIcon size={17} strokeWidth={2.2} className="shrink-0 text-ig-care" />
        話を聞いてくれる窓口があります
      </h3>
      <p className="mt-1 text-[13px] leading-[18px] text-ig-secondary">
        つらい気持ちを、専門の相談員に話すことができます。ひとりで抱えこまないでください。
      </p>
      {resources && resources.length > 0 ? (
        <ul className="mt-2.5 space-y-2.5">
          {resources.map((resource, index) => (
            <ResourceItem key={`${resource.name}-${index}`} resource={resource} />
          ))}
        </ul>
      ) : loading ? (
        <div className="mt-2.5 space-y-2.5" aria-busy="true" aria-label="相談窓口を読み込み中">
          {["w-40", "w-52"].map((width) => (
            <div key={width} className="rounded-xl bg-ig-elevated px-3 py-2.5">
              <Skeleton shape="text" className={width} />
              <Skeleton className="mt-2 h-8 w-36 rounded-lg" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl bg-ig-elevated px-3 py-2.5">
          <p className="text-[13px] leading-[18px] text-ig-secondary">
            相談窓口の一覧を読み込めませんでした。
          </p>
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex h-11 items-center px-1 text-[14px] font-semibold text-ig-blue-text pressable"
            >
              再読み込み
            </button>
          ) : null}
        </div>
      ) : null}
      <p className="mt-3 border-t border-ig-separator pt-2.5 text-[13px] leading-[18px]">
        命の危険が迫っているときは、すぐに{" "}
        <a href="tel:119" className="font-semibold text-ig-blue-text underline">
          119番
        </a>{" "}
        に電話してください。
      </p>
    </section>
  );
}

function ResourceItem({ resource }: { resource: SafetyResource }) {
  const tel = resource.phone ? telHref(resource.phone) : null;
  const url = resource.url ? safeExternalUrl(resource.url) : null;
  return (
    <li className="rounded-xl bg-ig-elevated px-3 py-2.5" data-testid="safety-resource">
      <p className="text-[14px] leading-[18px] font-semibold text-wrap-anywhere">{resource.name}</p>
      {resource.hours ? (
        <p className="mt-0.5 text-[12px] leading-4 text-ig-text">受付: {resource.hours}</p>
      ) : null}
      {tel || url ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          {tel && resource.phone ? (
            <a
              href={tel}
              aria-label={`${resource.name}に電話する（${resource.phone}）`}
              className="inline-flex h-11 items-center gap-1.5 rounded-lg bg-ig-bg px-3.5 text-[15px] font-semibold text-ig-text pressable"
            >
              <PhoneIcon size={17} strokeWidth={2} />
              {resource.phone}
            </a>
          ) : resource.phone ? (
            <span className="text-[14px] font-semibold">{resource.phone}</span>
          ) : null}
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${resource.name}のウェブサイトを開く（新しいタブ）`}
              className="inline-flex h-11 items-center gap-1 px-1 text-[14px] font-semibold text-ig-blue-text pressable"
            >
              ウェブサイト
              <ExternalLinkIcon size={15} strokeWidth={2.2} />
            </a>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
