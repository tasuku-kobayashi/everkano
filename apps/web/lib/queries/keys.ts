/**
 * TanStack Query のクエリキー（一元管理）。
 *
 * - 配列の先頭要素でリソースを分けた階層構造。前方一致で invalidate できる:
 *     queryClient.invalidateQueries({ queryKey: queryKeys.dm() })          // DM 関連すべて
 *     queryClient.invalidateQueries({ queryKey: queryKeys.post(postId) })  // 投稿 + そのコメント
 * - このファイルは基盤担当が管理する。機能固有のキーが必要な場合は各機能のファイルで
 *   `[...queryKeys.feed(), "something"] as const` のように派生させること。
 */

export type CharacterPostsTab = "free" | "paid";

export const queryKeys = {
  /** ログイン中ユーザー関連のプレフィックス */
  me: () => ["me"] as const,
  /** 自分の profiles 行（display_name / deleted_at） */
  profile: () => ["me", "profile"] as const,

  /** ホームフィード（useInfiniteQuery） */
  feed: () => ["feed"] as const,
  /** ホーム上部のストーリーズ風アバター行 */
  stories: () => ["feed", "stories"] as const,

  /** 個別投稿 */
  post: (postId: string) => ["posts", postId] as const,
  /** 投稿のコメント一覧（post のサブキー） */
  comments: (postId: string) => ["posts", postId, "comments"] as const,

  /** キャラクター（handle で取得） */
  character: (handle: string) => ["characters", "handle", handle] as const,
  /** キャラクター（id で取得。DM 画面など） */
  characterById: (characterId: string) => ["characters", "id", characterId] as const,
  /** キャラクターの投稿グリッド（無料/有料タブ） */
  characterPosts: (characterId: string, tab: CharacterPostsTab) =>
    ["characters", characterId, "posts", tab] as const,

  /** キャラ検索（空文字 = おすすめ一覧） */
  search: (query: string) => ["search", query] as const,

  /** 自分のいいね状態 */
  likes: () => ["likes"] as const,
  /** 投稿 1 件に対する自分のいいね状態 */
  like: (postId: string) => ["likes", postId] as const,

  /** DM 関連のプレフィックス */
  dm: () => ["dm"] as const,
  /** DM 一覧（RPC list_dm_threads） */
  dmThreads: () => ["dm", "threads"] as const,
  /** タブバーの未読バッジ（list_dm_threads の unread_count 合計） */
  dmUnreadTotal: () => ["dm", "unread-total"] as const,
  /** (自分, キャラ) の会話 */
  conversation: (characterId: string) => ["dm", "conversation", characterId] as const,
  /** 会話のメッセージ（過去ログは useInfiniteQuery で遡る） */
  messages: (conversationId: string) => ["dm", "messages", conversationId] as const,

  /** メモリパネル（そのキャラが覚えていること。置き換えられた古い記憶も含む） */
  memories: (characterId: string) => ["memories", characterId] as const,
  /** メモリパネルの約束・予定（そのキャラとの未達・話題にした約束） */
  promises: (characterId: string) => ["promises", characterId] as const,

  /** 自発メッセージの設定（全体 + キャラ別。GET /proactive/settings） */
  proactiveSettings: () => ["proactive-settings"] as const,

  /** E6: 相談窓口の一覧（GET /safety/resources。全員に同じ内容） */
  safetyResources: () => ["safety-resources"] as const,
} as const;

export type QueryKeys = typeof queryKeys;
