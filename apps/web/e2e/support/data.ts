import { sql, sqlOne } from "./db";

/**
 * シードデータから検証対象を選ぶ（読み取りのみ）。
 * iphone / android の 2 プロジェクトが並列に同じ DB を使うため、コメントを書き込むテストでは
 * プロジェクトごとに別の投稿を選ぶ（offset）。
 */

export interface PostRow {
  id: string;
  character_id: string;
  handle: string;
  name: string;
  is_paid: boolean;
  price_tokens: number;
  comment_count: number;
}

const POST_COLUMNS = `p.id, p.character_id, c.handle, c.name, p.is_paid, p.price_tokens,
  (select count(*)::int from public.comments cm where cm.post_id = p.id and cm.created_at <= now()) as comment_count`;

/**
 * 1 日以上前に公開された無料投稿のうち、キャラのトップレベルのコメントが 2 件以上あるものを古い順に offset 番目。
 * テストが書き込むコメント（ユーザーのコメントとキャラの返信）で並び順が変わらないよう、
 * テストが作らない「キャラのトップレベルのコメント」だけで絞り込み、公開日時の古い順（予約投稿の公開で
 * ずれない）に並べる。
 */
export function freePostWithComments(offset: number): Promise<PostRow> {
  return sqlOne<PostRow>(
    `select ${POST_COLUMNS}
       from public.posts p join public.characters c on c.id = p.character_id
      where not p.is_paid and c.is_active and p.published_at <= now() - interval '1 day'
        and (select count(*) from public.comments cm
              where cm.post_id = p.id and cm.author_type = 'character'
                and cm.parent_comment_id is null and cm.created_at <= now()) >= 2
      order by p.published_at, p.id
      offset $1 limit 1`,
    [offset],
  );
}

/** 公開済みの有料投稿（新しい順に offset 番目） */
export function paidPost(offset = 0): Promise<PostRow> {
  return sqlOne<PostRow>(
    `select ${POST_COLUMNS}
       from public.posts p join public.characters c on c.id = p.character_id
      where p.is_paid and p.published_at <= now() and c.is_active
      order by p.published_at desc, p.id desc
      offset $1 limit 1`,
    [offset],
  );
}

export interface ProfileCounts {
  total: number;
  free: number;
  paid: number;
  follower_count: number;
}

export function profileCounts(handle: string): Promise<ProfileCounts> {
  return sqlOne<ProfileCounts>(
    `select count(p.id)::int as total,
            count(p.id) filter (where not p.is_paid)::int as free,
            count(p.id) filter (where p.is_paid)::int as paid,
            c.follower_count
       from public.characters c
       left join public.posts p on p.character_id = c.id and p.published_at <= now()
      where c.handle = $1
      group by c.id`,
    [handle],
  );
}

export async function visiblePostIds(at: Date): Promise<string[]> {
  const rows = await sql<{ id: string }>(
    "select id from public.posts where published_at <= $1 order by published_at desc, id desc",
    [at],
  );
  return rows.map((row) => row.id);
}
