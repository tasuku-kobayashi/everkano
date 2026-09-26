# ADR-0020: コンテンツ管理（ペルソナ YAML → seed.sql の生成・予約投稿・成人のみの検証）

- ステータス: 採用（ペルソナ YAML の `engine:` セクションと検証を [ADR-0035](0035-character-engine-architecture.md)、エンジン用のシード（`seed_engine.sql` の画像プール）を [ADR-0040](0040-character-calendar.md) で追補）
- 日付: 2026-09-25
- 関連: 仕様書 §8.1・§11・§12「管理画面は作らない」・§18 / [ADR-0004](0004-schema-changes-from-spec.md) / 実装: `packages/personas/`（`*.yaml`, `seed/feed.yaml`, `scripts/generate_seed.py`, `scripts/validate_personas.py`）, `infra/supabase/seed.sql`, `apps/api/app/services/persona.py`

## コンテキスト

- 仕様書 §11: キャラ 10 体・投稿 40〜60 件（各キャラ 1〜2 件は有料）・コメント各投稿 2〜5 件を `seed.sql` で投入する。管理画面は作らない（§12）。
- キャラの人格はペルソナ YAML（§8.1）で定義し、API がシステムプロンプトに使う。同じキャラの `handle` / `name` / `avatar` / `bio` は
  `characters` テーブルにも入る（二重管理になりやすい）。
- 「この子は今日も生きている」と感じられるフィード（§18）には、投稿の時刻がキャラの生活リズムに合い、時間が経つと新しい投稿が現れることが効く。
- キャラクターは全員成人でなければならない。

## 決定

- **ペルソナ YAML（`packages/personas/<key>.yaml`）を人格の単一の正** とする。API は起動時に YAML を読み込んで検証し（`age` は 20 以上の整数、
  必須項目、未知のキーは無視）、違反があれば起動しない。YAML が無いキャラは `characters.system_prompt` から最小限のペルソナを組み立てる
  （フォールバック。起動時に WARNING、監査ログの `persona_fallback=true`）。
- 投稿・コメント・フォロワー数は `packages/personas/seed/feed.yaml` に書き、**`generate_seed.py` で `infra/supabase/seed.sql` を生成する**
  （手で編集しない。CI の `pnpm personas:validate` が「生成結果と一致するか」を検査する）。
  - ID は固定 UUID（キャラ `00000000-0000-4000-8000-0000000000c1`〜、投稿 `…-8001-…`、コメント `…-8002-…`）。feed.yaml の並び順が番号になるので、
    追加は末尾に行う。
  - `published_at` は **投入時刻（`now()`）からの相対値** で、日本時間でキャラの `schedule_pattern` に合う時刻に置く。24 時間以内の投稿
    （ストーリーズの行に出る）と、**未来の予約投稿（6 件）** を含む。予約投稿は RLS により公開時刻まで見えず、時間とともにフィードに現れる。
    コメントの `created_at` も投稿からの相対値（[ADR-0004](0004-schema-changes-from-spec.md) の 5）。
  - 画像は開発用のプレースホルダ URL（`api.dicebear.com` のイラスト / `picsum.photos`）。有料投稿はプレビューと本体を推測できない別キーにする
    （[ADR-0006](0006-paid-post-private-assets.md)）。実在人物の写真は使わない。
- **成人のみの検証** を 3 か所で行う: API の起動時（`age >= 20`）、`validate_personas.py`（年齢、未成年を想起させる語・20 歳未満の年齢表記が
  YAML / feed.yaml / seed.sql に無いこと、口調例に自分の NG ワードが無いこと 等）、CI（checks ジョブ）。
- 本番（ホスト版 Supabase）へのシード投入は初回の `supabase db push --include-seed` の 1 回だけ。以後のキャラ・投稿の追加は差分の SQL で行う
  （[06-operations.md](../handover/06-operations.md#キャラクターを追加する)）。

## 結果・トレードオフ

- シードの内容は 10 体 / 投稿 50 件（有料 10 件）/ `post_private_assets` 10 件 / コメント 142 件（2026-09-25 時点の生成結果）。
- 口調・プロフィール・挨拶など **YAML だけで使う項目は API の再デプロイで反映** される（YAML はイメージに入っている）。`characters` の列
  （表示名・アバター・自己紹介）を変える場合は DB の更新も必要。
- シードは投入時刻基準なので、数日経つと「24 時間以内の投稿」が減り、予約投稿もすべて公開済みになる。ローカルのデモ前は `pnpm db:reset`
  （ローカルの全データを作り直す）で入れ直す。本番では定期的に新しい投稿（予約投稿を含む）を SQL で足す運用が必要。
- コンテンツの追加に SQL の知識が要る（管理画面はスコープ外）。

## 代替案

- **seed.sql を手書きする**: YAML と `characters` の値がずれ、未成年表現の検査も通しにくい。
- **ペルソナを DB に持つ（API は DB から読む）**: 再デプロイ無しで口調を変えられるが、レビュー（PR）と検証（CI）の仕組みから外れる。
  管理画面を作る段階で再検討する。
- **絶対日時のシード**: 投入のたびに古い日付のフィードになる。相対日時にした。
