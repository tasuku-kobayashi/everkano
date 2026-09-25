# ADR-0005: ベクトル索引 HNSW と user×character 内の厳密検索

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §6・§9.3 / [ADR-0009](0009-memory-engine.md) / 実装: マイグレーションの `memories_embedding_hnsw_idx`, `apps/api/app/services/memory.py`（`_RETRIEVE_SQL`, `_NEAREST_SQL`）

## コンテキスト

- 仕様書 §6 は `memories.embedding` に ivfflat 索引を、§9.3 は「pgvector でコサイン類似度上位 5 件を取得 → `importance` で再ランク」を指定している。
- ivfflat は作成時点のデータでリストの重心を学習する。空のテーブル（マイグレーション直後）に作ると重心が学習されず、データが増えても
  作り直すまで再現率が著しく低い。
- DM 応答時の検索は常に「このユーザー × このキャラ」の記憶に絞る。近似索引（ivfflat / HNSW）は全体から近傍候補を取り出してから
  `where user_id = ... and character_id = ...` で絞るため、他のユーザーの記憶が候補を占めると **該当ペアの記憶を取りこぼす**
  （5 件未満になる、または本当に近い記憶が入らない）。
- 「覚えているはずのことを思い出さない」は、このプロダクトの価値（仕様書 §18「自分のことを覚えている」）を直接損なう。

## 決定

1. 索引は **HNSW**（`memories_embedding_hnsw_idx`, `vector_cosine_ops`）で作る。空のテーブルに作っても劣化しない。
   この索引は将来の横断検索用で、DM 応答では使わない。
2. DM 応答時の長期記憶の検索は、(user_id, character_id) で絞った行について **全件の距離を計算する厳密検索** にする。
   `with pair as materialized (...)` で先に対象行を確定させ、プランナーが HNSW 索引を使わないようにする。
   絞り込みには `memories_user_id_character_id_idx` が効く。
3. 上位 `MEMORY_RETRIEVAL_TOP_K`（5）件を類似度で取り、`importance` の降順（同点は類似度）で再ランクする。中期要約（`summary` タグ）の
   最新 2 件は常に含める（重複は除く）。
4. 記憶の保存時の重複排除（コサイン類似度 `>= MEMORY_DEDUP_SIMILARITY`）も同じ方式の厳密検索にする（`summary` タグは対象外）。

## 結果・トレードオフ

- 1 ペアの記憶は多くても数百〜数千件の想定で、厳密検索で十分速い。数万件規模になると遅くなるので、その場合は古い記憶の要約・統合、
  または (user_id, character_id) でのパーティショニングを新しい ADR で検討する。
- HNSW 索引は書き込みのたびに更新されるが、DM 応答では使っていない。書き込み負荷が問題になれば削除してよい（横断検索を作るまで不要）。
- 埋め込みの方式（`EMBEDDING_MODE=hash` / `live`、`EMBEDDING_MODEL`）を切り替えるとベクトル空間が変わる。次元数が同じ 1536 なので
  エラーにならず、検索と重複排除が黙って壊れる。切り替えたら `apps/api/scripts/reembed_memories.py` で全件を再埋め込みする
  （[ADR-0008](0008-llm-embedding-providers-and-mock.md)、[06-operations.md](../handover/06-operations.md#埋め込み設定の切り替え)）。

## 代替案

- **仕様どおり ivfflat**: 空テーブルでの作成で再現率が落ちるうえ、ペアで絞る検索の取りこぼしは解決しない。
- **HNSW + `hnsw.ef_search` を大きくする / pgvector の iterative index scan**: 取りこぼしは減るが保証はない。
- **ペアごとの部分索引**: ユーザー数 × キャラ数の索引は現実的でない。
