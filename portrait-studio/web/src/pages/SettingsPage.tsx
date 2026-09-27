import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { marked } from "marked";
import { useLocation } from "react-router-dom";
import { api, getErrorMessage, unwrap, type ScenePreset } from "../api/client";
import { keys, useAudit, useCharacters, useCompliance, useHealth, useScenes, useVramTable } from "../api/queries";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { formatDate, formatMb } from "../lib/format";
import { useUiStore } from "../store/ui";

/** Screen 5: API key / connection, VRAM table + thresholds, presets, audit log, retention, compliance. */
export function SettingsPage() {
  const location = useLocation() as { state?: { reason?: string } };
  const reason = location.state?.reason;
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4" data-testid="settings">
      {reason === "no-key" && (
        <div className="rounded border border-accent bg-accent-soft/40 p-3 text-sm" data-testid="first-run">
          <strong>はじめに API キーを設定してください。</strong> `.env` の <code className="font-mono">API_KEY</code> と同じ値を入力すると各画面が使えます（このブラウザの localStorage に保存されます）。
        </div>
      )}
      {reason === "unauthorized" && <div className="rounded border border-rose-700 bg-rose-950/60 p-3 text-sm text-rose-100">API キーが拒否されました（401）。値を確認してください。</div>}
      <ApiKeySection />
      <ConnectionSection />
      <DisplaySection />
      <VramSection />
      <PresetsSection />
      <AuditSection />
      <RetentionSection />
      <ComplianceSection />
    </div>
  );
}

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <section className="card p-4" data-testid={testId}>
      <h2 className="mb-3 text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function ApiKeySection() {
  const apiKey = useUiStore((s) => s.apiKey);
  const setApiKey = useUiStore((s) => s.setApiKey);
  const [value, setValue] = useState(apiKey);
  const [show, setShow] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const save = async () => {
    setApiKey(value.trim());
    await qc.invalidateQueries();
    try {
      const r = await api.GET("/api/characters", { params: { query: { sort: "recent" } } });
      if (r.response.status === 401) toast.error("API キーが拒否されました（401）");
      else toast.success("API キーを保存し、接続を確認しました");
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  return (
    <Section title="API キー" testId="api-key-section">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex-1">
          <span className="label">X-API-Key（.env の API_KEY）</span>
          <input className="input font-mono" type={show ? "text" : "password"} value={value} onChange={(e) => setValue(e.target.value)} data-testid="api-key-input" autoComplete="off" />
        </label>
        <button className="btn-secondary" onClick={() => setShow((s) => !s)}>
          {show ? "隠す" : "表示"}
        </button>
        <button className="btn-primary" onClick={save} disabled={value.trim().length < 8} data-testid="api-key-save">
          保存して接続確認
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        ローカル専用のため localStorage と Cookie（画像タグ用）に保存します。外部に公開する構成に変える場合はこの方式を必ず変更してください（README 参照）。
      </p>
    </Section>
  );
}

function ConnectionSection() {
  const { data, refetch, isFetching } = useHealth();
  const rows: Array<[string, string]> = data
    ? [
        ["状態", data.ok ? "接続中" : `未接続: ${data.error ?? ""}`],
        ["GPU", data.gpu ?? "—"],
        ["VRAM", data.vram_total_mb ? `${formatMb(data.vram_total_mb)}（空き ${formatMb(data.vram_free_mb)}）` : "—"],
        ["torch", data.torch ?? "—"],
        ["CUDA", data.cuda ?? "—"],
        ["ComfyUI", data.comfy_version ?? "—"],
        ["顔一貫性の手法", data.face_methods?.length ? data.face_methods.join(", ") : "（ワークフロー検証待ち / 未登録）"],
        ["チェックポイント", data.checkpoints?.length ? data.checkpoints.join(", ") : "なし"],
        ["顔分析エンジン", `${data.face_engine}${data.face_engine_ready ? "（ロード済み）" : "（初回使用時にロード）"}`],
        ["API バージョン", data.version],
      ]
    : [];
  return (
    <Section title="接続先（GET /api/health）" testId="connection-section">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-400">{k}</dt>
            <dd className="break-all font-mono text-xs" data-testid={`health-${k}`}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
      {data?.workflows?.some((w) => !w.ok || (w.issues ?? []).length) && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-amber-300">ワークフロー検証の指摘</summary>
          <ul className="mt-1 list-disc pl-5">
            {(data.workflows ?? []).flatMap((w) => (w.issues ?? []).map((i) => (
              <li key={w.method + i} className={i.startsWith("error") ? "text-rose-300" : "text-amber-200"}>
                {w.file}: {i}
              </li>
            )))}
          </ul>
        </details>
      )}
      <button className="btn-secondary mt-3" onClick={() => refetch()} disabled={isFetching}>
        再確認
      </button>
    </Section>
  );
}

function DisplaySection() {
  const blur = useUiStore((s) => s.blurDefault);
  const setBlur = useUiStore((s) => s.setBlurDefault);
  const sim = useUiStore((s) => s.similarity);
  const setSim = useUiStore((s) => s.setSimilarity);
  const warn = useUiStore((s) => s.vramWarnPercent);
  const setWarn = useUiStore((s) => s.setVramWarnPercent);
  return (
    <Section title="表示と警告のしきい値" testId="display-section">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={!blur} onChange={(e) => setBlur(!e.target.checked)} data-testid="blur-toggle" />
        NSFW ぼかしを既定で解除する（既定はぼかし ON。画面共有時の事故防止）
      </label>
      <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
        <label>
          <span className="label">類似度: 良好の下限 ({sim.good.toFixed(2)})</span>
          <input type="range" min={0.5} max={0.95} step={0.01} value={sim.good} onChange={(e) => setSim({ ...sim, good: Number(e.target.value) })} className="w-full" />
        </label>
        <label>
          <span className="label">類似度: 許容の下限 ({sim.acceptable.toFixed(2)})</span>
          <input type="range" min={0.3} max={0.9} step={0.01} value={sim.acceptable} onChange={(e) => setSim({ ...sim, acceptable: Number(e.target.value) })} className="w-full" />
        </label>
        <label>
          <span className="label">VRAM 使用率の警告 ({warn}%)</span>
          <input type="range" min={50} max={100} step={1} value={warn} onChange={(e) => setWarn(Number(e.target.value))} className="w-full" />
        </label>
      </div>
      <p className="mt-2 text-xs text-slate-500">初期値 0.75 / 0.60 は目安です。scripts/face_similarity.py で同一キャラ同士・別キャラ同士のペアを実測して較正し、docs/MODELS.md に根拠を残してください。</p>
    </Section>
  );
}

function VramSection() {
  const { data } = useVramTable();
  return (
    <Section title="VRAM 実測テーブル（scripts/measure_vram.py の結果。推測値は入れない）" testId="vram-section">
      <p className="mb-2 text-xs text-slate-400">
        GPU: {data?.gpu ?? "未実測"} / 総量: {data?.vram_total_mb ? formatMb(data.vram_total_mb) : "—"} / 実測日時: {data?.measured_at ?? "未実測"}。 未実測（peak が空）の組み合わせは安全側に倒して生成を拒否します。
      </p>
      <table className="w-full text-xs">
        <thead className="text-slate-400">
          <tr>
            <th className="p-1 text-left">手法</th>
            <th className="p-1 text-left">解像度</th>
            <th className="p-1 text-left">upscale</th>
            <th className="p-1 text-left">face_detailer</th>
            <th className="p-1 text-right">ピーク VRAM</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {data?.entries.map((e, i) => (
            <tr key={i} className="border-t border-ink-700">
              <td className="p-1">{e.method}</td>
              <td className="p-1">
                {e.width}×{e.height}
              </td>
              <td className="p-1">{e.upscale}</td>
              <td className="p-1">{e.face_detailer ? "有" : "無"}</td>
              <td className={e.peak_mb === null ? "p-1 text-right text-amber-300" : "p-1 text-right"}>{e.peak_mb === null ? "未実測" : `${e.peak_mb} MB`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <FreeVramButton />
    </Section>
  );
}

function FreeVramButton() {
  const toast = useToast();
  const qc = useQueryClient();
  return (
    <button
      className="btn-secondary mt-3"
      onClick={async () => {
        try {
          const r = unwrap(await api.POST("/api/system/free"));
          toast.success(`VRAM を解放しました: 空き ${r.free_mb_before} MB → ${r.free_mb_after} MB`);
          void qc.invalidateQueries({ queryKey: keys.vram });
        } catch (e) {
          toast.error(getErrorMessage(e));
        }
      }}
    >
      VRAM を解放（ComfyUI /free）
    </button>
  );
}

function PresetsSection() {
  const { data: scenes } = useScenes();
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<ScenePreset | null>(null);
  const save = async () => {
    if (!editing) return;
    try {
      unwrap(await api.POST("/api/presets/scenes", { body: { ...editing, fragments: editing.fragments ?? [] } }));
      toast.success("保存しました");
      setEditing(null);
      void qc.invalidateQueries({ queryKey: keys.scenes });
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  const remove = async (id: string, builtin: boolean) => {
    try {
      unwrap(await api.DELETE("/api/presets/scenes/{scene_id}", { params: { path: { scene_id: id } } }));
      toast.success(builtin ? "既定の内容に戻しました" : "削除しました");
      void qc.invalidateQueries({ queryKey: keys.scenes });
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };
  return (
    <Section title="シーンプリセット（プロンプト断片の配列。キャラの固定プレフィックスと自動合成）" testId="presets-section">
      <ul className="space-y-1 text-xs">
        {scenes?.map((s) => (
          <li key={s.id} className="flex items-start justify-between gap-2 rounded bg-ink-800 p-2">
            <div className="min-w-0">
              <span className="font-medium">{s.name}</span> <span className="font-mono text-slate-500">{s.id}</span> {s.verify && <span className="badge bg-ink-700">検証用</span>}
              <div className="truncate font-mono text-slate-400">{(s.fragments ?? []).join(", ")}</div>
            </div>
            <div className="flex shrink-0 gap-1">
              <button className="btn-ghost" onClick={() => setEditing({ ...s, fragments: s.fragments ?? [] })}>
                編集
              </button>
              <button className="btn-ghost text-rose-300" onClick={() => remove(s.id, !!s.builtin)}>
                {s.builtin ? "既定に戻す" : "削除"}
              </button>
            </div>
          </li>
        ))}
      </ul>
      <button className="btn-secondary mt-2" onClick={() => setEditing({ id: "", name: "", fragments: [], description: "", builtin: false, verify: false })}>
        ＋ プリセットを追加
      </button>
      {editing && (
        <div className="mt-3 grid gap-2 text-sm md:grid-cols-2">
          <label>
            <span className="label">id（英小文字・数字・_）</span>
            <input className="input font-mono" value={editing.id} disabled={!!editing.builtin} onChange={(e) => setEditing({ ...editing, id: e.target.value })} />
          </label>
          <label>
            <span className="label">名前</span>
            <input className="input" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          </label>
          <label className="md:col-span-2">
            <span className="label">断片（1 行 1 つ）</span>
            <textarea className="input font-mono" rows={4} value={(editing.fragments ?? []).join("\n")} onChange={(e) => setEditing({ ...editing, fragments: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })} />
          </label>
          <div className="flex gap-2 md:col-span-2">
            <button className="btn-primary" onClick={save} disabled={!editing.id || !editing.name}>
              保存
            </button>
            <button className="btn-secondary" onClick={() => setEditing(null)}>
              キャンセル
            </button>
          </div>
        </div>
      )}
    </Section>
  );
}

function AuditSection() {
  const [limit, setLimit] = useState(50);
  const { data } = useAudit(limit);
  return (
    <Section title="監査ログ（logs/audit.jsonl の末尾。全生成リクエストを記録）" testId="audit-section">
      <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
        <span>
          {data?.path} · 全 {data?.total_lines ?? 0} 行
        </span>
        <select className="input w-24" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[20, 50, 100, 500].map((n) => (
            <option key={n} value={n}>
              {n} 件
            </option>
          ))}
        </select>
      </div>
      <div className="max-h-72 overflow-auto">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-ink-900 text-slate-400">
            <tr>
              <th className="p-1 text-left">時刻</th>
              <th className="p-1 text-left">endpoint</th>
              <th className="p-1 text-left">状態</th>
              <th className="p-1 text-left">キャラ</th>
              <th className="p-1 text-left">手法 / weight</th>
              <th className="p-1 text-left">seed</th>
              <th className="p-1 text-left">枚数</th>
              <th className="p-1 text-left">類似度</th>
              <th className="p-1 text-left">所要</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {data?.items.map((row, i) => {
              const r = row as Record<string, unknown>;
              const sims = (r.similarity_scores as Array<number | null> | undefined) ?? [];
              return (
                <tr key={i} className="border-t border-ink-700">
                  <td className="p-1">{formatDate(String(r.timestamp ?? ""))}</td>
                  <td className="p-1">{String(r.endpoint ?? "")}</td>
                  <td className="p-1">{String(r.status ?? "")}</td>
                  <td className="p-1">{r.character_id ? `${String(r.character_id).slice(-6)} v${r.character_version ?? ""}` : "—"}</td>
                  <td className="p-1">
                    {String(r.face_method ?? "—")} / {String(r.face_weight ?? "—")}
                  </td>
                  <td className="p-1">{String(r.seed ?? "")}</td>
                  <td className="p-1">{String(r.count ?? "")}</td>
                  <td className="p-1">{sims.map((s) => (s === null ? "—" : s.toFixed(2))).join(" ")}</td>
                  <td className="p-1">{r.duration_ms ? `${Math.round(Number(r.duration_ms) / 1000)} s` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function RetentionSection() {
  const { data: characters } = useCharacters({ include_drafts: true });
  const [confirm, setConfirm] = useState(false);
  const [withImages, setWithImages] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const drafts = useMemo(() => characters?.filter((c) => c.status === "draft") ?? [], [characters]);
  const deleteAll = async () => {
    let n = 0;
    for (const c of characters ?? []) {
      try {
        unwrap(await api.DELETE("/api/characters/{character_id}", { params: { path: { character_id: c.id }, query: { delete_images: withImages } } }));
        n += 1;
      } catch (e) {
        toast.error(getErrorMessage(e));
      }
    }
    toast.success(`${n} 体を削除しました`);
    setConfirm(false);
    void qc.invalidateQueries();
  };
  const deleteDrafts = async () => {
    for (const c of drafts) await api.DELETE("/api/characters/{character_id}", { params: { path: { character_id: c.id }, query: { delete_images: true } } });
    toast.success(`下書き ${drafts.length} 体を削除しました`);
    void qc.invalidateQueries();
  };
  return (
    <Section title="参照顔の保持と削除" testId="retention-section">
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
        <li>参照顔（アップロード / 選定した画像のコピー）と ArcFace 埋め込みは、キャラクターを削除するまで <code className="font-mono">data/refs/&lt;id&gt;/</code> に保持されます。自動削除はありません。</li>
        <li>キャラクターの削除（一覧のメニュー、または下のボタン）で全版の参照顔と埋め込みを即時に削除します。生成画像は既定で残り、チェックを入れると画像とサイドカー JSON も削除します。</li>
        <li>監査ログ <code className="font-mono">logs/audit.jsonl</code> は削除しません（来歴の記録）。運用方針は docs/COMPLIANCE.md を参照。</li>
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <button className="btn-secondary" onClick={deleteDrafts} disabled={!drafts.length}>
          下書きキャラを削除（{drafts.length} 体）
        </button>
        <button className="btn-danger" onClick={() => setConfirm(true)} disabled={!characters?.length} data-testid="delete-all-characters">
          すべてのキャラクターを削除（{characters?.length ?? 0} 体）
        </button>
      </div>
      <ConfirmDialog open={confirm} title="すべてのキャラクターを削除" confirmLabel="すべて削除" onCancel={() => setConfirm(false)} onConfirm={deleteAll}>
        <p>全キャラクターの参照顔・埋め込み・版の履歴が削除され、復元できません。</p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={withImages} onChange={(e) => setWithImages(e.target.checked)} /> 生成画像もディスクから削除する
        </label>
      </ConfirmDialog>
    </Section>
  );
}

function ComplianceSection() {
  const { data, error } = useCompliance();
  const [html, setHtml] = useState("");
  useEffect(() => {
    if (!data) return;
    Promise.resolve(marked.parse(data.markdown, { async: false }) as string).then(setHtml);
  }, [data]);
  return (
    <Section title="法務・禁止事項（docs/COMPLIANCE.md）" testId="compliance-section">
      {error && <p className="text-sm text-rose-300">{getErrorMessage(error)}</p>}
      <article className="prose-invert max-w-none text-sm [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_p]:my-2 [&_a]:text-accent [&_table]:text-xs [&_td]:border [&_td]:border-ink-700 [&_td]:p-1 [&_th]:border [&_th]:border-ink-700 [&_th]:p-1" dangerouslySetInnerHTML={{ __html: html }} data-testid="compliance-body" />
    </Section>
  );
}
