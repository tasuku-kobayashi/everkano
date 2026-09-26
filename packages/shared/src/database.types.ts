export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

export type Database = {
  public: {
    Tables: {
      affinity_history: {
        Row: {
          after: Json;
          before: Json;
          character_id: string;
          created_at: string;
          delta: Json;
          evaluator: string;
          id: number;
          manipulation_detected: boolean;
          reason: string | null;
          source_message_ids: string[];
          stage_after: string;
          stage_before: string;
          user_id: string;
        };
        Insert: {
          after: Json;
          before: Json;
          character_id: string;
          created_at?: string;
          delta: Json;
          evaluator: string;
          id?: number;
          manipulation_detected?: boolean;
          reason?: string | null;
          source_message_ids?: string[];
          stage_after: string;
          stage_before: string;
          user_id: string;
        };
        Update: {
          after?: Json;
          before?: Json;
          character_id?: string;
          created_at?: string;
          delta?: Json;
          evaluator?: string;
          id?: number;
          manipulation_detected?: boolean;
          reason?: string | null;
          source_message_ids?: string[];
          stage_after?: string;
          stage_before?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "affinity_history_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "affinity_history_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      affinity_states: {
        Row: {
          absence_days: number | null;
          absence_return_at: string | null;
          awkwardness: number;
          character_id: string;
          closeness: number;
          created_at: string;
          daily_date: string | null;
          daily_delta: Json;
          discontent: number;
          evaluated_until: string | null;
          last_decayed_at: string | null;
          last_interaction_at: string | null;
          possessiveness: number;
          romance: number;
          stage: string;
          stage_candidate: string | null;
          stage_candidate_since: string | null;
          stage_candidate_turns: number;
          stage_changed_at: string | null;
          tension_high_since: string | null;
          trust: number;
          updated_at: string;
          user_id: string;
          user_turns: number;
        };
        Insert: {
          absence_days?: number | null;
          absence_return_at?: string | null;
          awkwardness?: number;
          character_id: string;
          closeness?: number;
          created_at?: string;
          daily_date?: string | null;
          daily_delta?: Json;
          discontent?: number;
          evaluated_until?: string | null;
          last_decayed_at?: string | null;
          last_interaction_at?: string | null;
          possessiveness?: number;
          romance?: number;
          stage?: string;
          stage_candidate?: string | null;
          stage_candidate_since?: string | null;
          stage_candidate_turns?: number;
          stage_changed_at?: string | null;
          tension_high_since?: string | null;
          trust?: number;
          updated_at?: string;
          user_id: string;
          user_turns?: number;
        };
        Update: {
          absence_days?: number | null;
          absence_return_at?: string | null;
          awkwardness?: number;
          character_id?: string;
          closeness?: number;
          created_at?: string;
          daily_date?: string | null;
          daily_delta?: Json;
          discontent?: number;
          evaluated_until?: string | null;
          last_decayed_at?: string | null;
          last_interaction_at?: string | null;
          possessiveness?: number;
          romance?: number;
          stage?: string;
          stage_candidate?: string | null;
          stage_candidate_since?: string | null;
          stage_candidate_turns?: number;
          stage_changed_at?: string | null;
          tension_high_since?: string | null;
          trust?: number;
          updated_at?: string;
          user_id?: string;
          user_turns?: number;
        };
        Relationships: [
          {
            foreignKeyName: "affinity_states_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "affinity_states_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      audit_logs: {
        Row: {
          character_id: string | null;
          created_at: string;
          event_type: string;
          id: number;
          payload: Json;
          user_id: string | null;
        };
        Insert: {
          character_id?: string | null;
          created_at?: string;
          event_type: string;
          id?: number;
          payload: Json;
          user_id?: string | null;
        };
        Update: {
          character_id?: string | null;
          created_at?: string;
          event_type?: string;
          id?: number;
          payload?: Json;
          user_id?: string | null;
        };
        Relationships: [];
      };
      character_events: {
        Row: {
          busyness: number;
          character_id: string;
          created_at: string;
          description: string | null;
          ends_at: string;
          generated_for: string | null;
          id: string;
          kind: string;
          location: string | null;
          meta: Json;
          mood: string | null;
          participants: string[];
          post_id: string | null;
          source: string;
          source_key: string | null;
          starts_at: string;
          status: string;
          title: string;
          user_id: string | null;
          visibility: string;
        };
        Insert: {
          busyness?: number;
          character_id: string;
          created_at?: string;
          description?: string | null;
          ends_at: string;
          generated_for?: string | null;
          id?: string;
          kind: string;
          location?: string | null;
          meta?: Json;
          mood?: string | null;
          participants?: string[];
          post_id?: string | null;
          source?: string;
          source_key?: string | null;
          starts_at: string;
          status?: string;
          title: string;
          user_id?: string | null;
          visibility?: string;
        };
        Update: {
          busyness?: number;
          character_id?: string;
          created_at?: string;
          description?: string | null;
          ends_at?: string;
          generated_for?: string | null;
          id?: string;
          kind?: string;
          location?: string | null;
          meta?: Json;
          mood?: string | null;
          participants?: string[];
          post_id?: string | null;
          source?: string;
          source_key?: string | null;
          starts_at?: string;
          status?: string;
          title?: string;
          user_id?: string | null;
          visibility?: string;
        };
        Relationships: [
          {
            foreignKeyName: "character_events_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "character_events_post_id_fkey";
            columns: ["post_id"];
            isOneToOne: false;
            referencedRelation: "posts";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "character_events_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      character_memories: {
        Row: {
          character_id: string;
          content: string;
          created_at: string;
          embedding: string | null;
          id: string;
          kind: string;
          occurred_at: string | null;
          source_event_id: string | null;
          source_message_id: string | null;
          user_id: string | null;
        };
        Insert: {
          character_id: string;
          content: string;
          created_at?: string;
          embedding?: string | null;
          id?: string;
          kind?: string;
          occurred_at?: string | null;
          source_event_id?: string | null;
          source_message_id?: string | null;
          user_id?: string | null;
        };
        Update: {
          character_id?: string;
          content?: string;
          created_at?: string;
          embedding?: string | null;
          id?: string;
          kind?: string;
          occurred_at?: string | null;
          source_event_id?: string | null;
          source_message_id?: string | null;
          user_id?: string | null;
        };
        Relationships: [
          {
            foreignKeyName: "character_memories_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "character_memories_source_event_id_fkey";
            columns: ["source_event_id"];
            isOneToOne: false;
            referencedRelation: "character_events";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "character_memories_source_message_id_fkey";
            columns: ["source_message_id"];
            isOneToOne: false;
            referencedRelation: "messages";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "character_memories_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      character_states: {
        Row: {
          activity: string;
          busyness: number;
          character_id: string;
          event_id: string | null;
          location: string | null;
          mood: string | null;
          status_label: string | null;
          updated_at: string;
        };
        Insert: {
          activity: string;
          busyness?: number;
          character_id: string;
          event_id?: string | null;
          location?: string | null;
          mood?: string | null;
          status_label?: string | null;
          updated_at?: string;
        };
        Update: {
          activity?: string;
          busyness?: number;
          character_id?: string;
          event_id?: string | null;
          location?: string | null;
          mood?: string | null;
          status_label?: string | null;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "character_states_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: true;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "character_states_event_id_fkey";
            columns: ["event_id"];
            isOneToOne: false;
            referencedRelation: "character_events";
            referencedColumns: ["id"];
          },
        ];
      };
      characters: {
        Row: {
          avatar_url: string;
          bio: string | null;
          created_at: string;
          follower_count: number;
          handle: string;
          id: string;
          is_active: boolean;
          name: string;
          persona_key: string;
          system_prompt: string;
        };
        Insert: {
          avatar_url: string;
          bio?: string | null;
          created_at?: string;
          follower_count?: number;
          handle: string;
          id?: string;
          is_active?: boolean;
          name: string;
          persona_key: string;
          system_prompt: string;
        };
        Update: {
          avatar_url?: string;
          bio?: string | null;
          created_at?: string;
          follower_count?: number;
          handle?: string;
          id?: string;
          is_active?: boolean;
          name?: string;
          persona_key?: string;
          system_prompt?: string;
        };
        Relationships: [];
      };
      comments: {
        Row: {
          author_character_id: string | null;
          author_type: string;
          author_user_id: string | null;
          body: string;
          created_at: string;
          id: string;
          parent_comment_id: string | null;
          post_id: string;
        };
        Insert: {
          author_character_id?: string | null;
          author_type: string;
          author_user_id?: string | null;
          body: string;
          created_at?: string;
          id?: string;
          parent_comment_id?: string | null;
          post_id: string;
        };
        Update: {
          author_character_id?: string | null;
          author_type?: string;
          author_user_id?: string | null;
          body?: string;
          created_at?: string;
          id?: string;
          parent_comment_id?: string | null;
          post_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "comments_author_character_id_fkey";
            columns: ["author_character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "comments_author_user_id_fkey";
            columns: ["author_user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "comments_parent_comment_id_fkey";
            columns: ["parent_comment_id"];
            isOneToOne: false;
            referencedRelation: "comments";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "comments_post_id_fkey";
            columns: ["post_id"];
            isOneToOne: false;
            referencedRelation: "posts";
            referencedColumns: ["id"];
          },
        ];
      };
      conversations: {
        Row: {
          analyzed_until: string | null;
          character_id: string;
          created_at: string;
          id: string;
          last_message_at: string;
          summary_cursor: string | null;
          user_id: string;
          user_last_read_at: string;
        };
        Insert: {
          analyzed_until?: string | null;
          character_id: string;
          created_at?: string;
          id?: string;
          last_message_at?: string;
          summary_cursor?: string | null;
          user_id: string;
          user_last_read_at?: string;
        };
        Update: {
          analyzed_until?: string | null;
          character_id?: string;
          created_at?: string;
          id?: string;
          last_message_at?: string;
          summary_cursor?: string | null;
          user_id?: string;
          user_last_read_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "conversations_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "conversations_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      engine_jobs: {
        Row: {
          attempts: number;
          created_at: string;
          dedupe_key: string | null;
          finished_at: string | null;
          id: number;
          kind: string;
          last_error: string | null;
          locked_at: string | null;
          locked_by: string | null;
          max_attempts: number;
          payload: Json;
          run_at: string;
          status: string;
          updated_at: string;
        };
        Insert: {
          attempts?: number;
          created_at?: string;
          dedupe_key?: string | null;
          finished_at?: string | null;
          id?: number;
          kind: string;
          last_error?: string | null;
          locked_at?: string | null;
          locked_by?: string | null;
          max_attempts?: number;
          payload?: Json;
          run_at?: string;
          status?: string;
          updated_at?: string;
        };
        Update: {
          attempts?: number;
          created_at?: string;
          dedupe_key?: string | null;
          finished_at?: string | null;
          id?: number;
          kind?: string;
          last_error?: string | null;
          locked_at?: string | null;
          locked_by?: string | null;
          max_attempts?: number;
          payload?: Json;
          run_at?: string;
          status?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      engine_schedules: {
        Row: {
          last_error: string | null;
          last_run_at: string | null;
          name: string;
          next_run_at: string | null;
          updated_at: string;
        };
        Insert: {
          last_error?: string | null;
          last_run_at?: string | null;
          name: string;
          next_run_at?: string | null;
          updated_at?: string;
        };
        Update: {
          last_error?: string | null;
          last_run_at?: string | null;
          name?: string;
          next_run_at?: string | null;
          updated_at?: string;
        };
        Relationships: [];
      };
      likes: {
        Row: {
          created_at: string;
          post_id: string;
          user_id: string;
        };
        Insert: {
          created_at?: string;
          post_id: string;
          user_id: string;
        };
        Update: {
          created_at?: string;
          post_id?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "likes_post_id_fkey";
            columns: ["post_id"];
            isOneToOne: false;
            referencedRelation: "posts";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "likes_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      memories: {
        Row: {
          character_id: string;
          content: string;
          created_at: string;
          embedding: string | null;
          id: string;
          importance: number;
          is_user_edited: boolean;
          kind: string;
          last_referenced_at: string | null;
          reference_count: number;
          source_conversation_id: string | null;
          source_message_id: string | null;
          status: string;
          superseded_at: string | null;
          superseded_by: string | null;
          tags: string[];
          updated_at: string;
          user_id: string;
        };
        Insert: {
          character_id: string;
          content: string;
          created_at?: string;
          embedding?: string | null;
          id?: string;
          importance?: number;
          is_user_edited?: boolean;
          kind?: string;
          last_referenced_at?: string | null;
          reference_count?: number;
          source_conversation_id?: string | null;
          source_message_id?: string | null;
          status?: string;
          superseded_at?: string | null;
          superseded_by?: string | null;
          tags?: string[];
          updated_at?: string;
          user_id: string;
        };
        Update: {
          character_id?: string;
          content?: string;
          created_at?: string;
          embedding?: string | null;
          id?: string;
          importance?: number;
          is_user_edited?: boolean;
          kind?: string;
          last_referenced_at?: string | null;
          reference_count?: number;
          source_conversation_id?: string | null;
          source_message_id?: string | null;
          status?: string;
          superseded_at?: string | null;
          superseded_by?: string | null;
          tags?: string[];
          updated_at?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "memories_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "memories_source_conversation_id_fkey";
            columns: ["source_conversation_id"];
            isOneToOne: false;
            referencedRelation: "conversations";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "memories_source_message_id_fkey";
            columns: ["source_message_id"];
            isOneToOne: false;
            referencedRelation: "messages";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "memories_superseded_by_fkey";
            columns: ["superseded_by"];
            isOneToOne: false;
            referencedRelation: "memories";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "memories_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      memory_tombstones: {
        Row: {
          character_id: string;
          content_hash: string;
          deleted_at: string;
          embedding: string | null;
          id: string;
          kind: string;
          user_id: string;
        };
        Insert: {
          character_id: string;
          content_hash: string;
          deleted_at?: string;
          embedding?: string | null;
          id?: string;
          kind: string;
          user_id: string;
        };
        Update: {
          character_id?: string;
          content_hash?: string;
          deleted_at?: string;
          embedding?: string | null;
          id?: string;
          kind?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "memory_tombstones_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "memory_tombstones_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      messages: {
        Row: {
          body: string;
          conversation_id: string;
          created_at: string;
          id: string;
          is_proactive: boolean;
          safety_triggered: boolean;
          sender_type: string;
        };
        Insert: {
          body: string;
          conversation_id: string;
          created_at?: string;
          id?: string;
          is_proactive?: boolean;
          safety_triggered?: boolean;
          sender_type: string;
        };
        Update: {
          body?: string;
          conversation_id?: string;
          created_at?: string;
          id?: string;
          is_proactive?: boolean;
          safety_triggered?: boolean;
          sender_type?: string;
        };
        Relationships: [
          {
            foreignKeyName: "messages_conversation_id_fkey";
            columns: ["conversation_id"];
            isOneToOne: false;
            referencedRelation: "conversations";
            referencedColumns: ["id"];
          },
        ];
      };
      post_image_pool: {
        Row: {
          character_id: string | null;
          created_at: string;
          id: string;
          image_url: string;
          tags: string[];
        };
        Insert: {
          character_id?: string | null;
          created_at?: string;
          id?: string;
          image_url: string;
          tags?: string[];
        };
        Update: {
          character_id?: string | null;
          created_at?: string;
          id?: string;
          image_url?: string;
          tags?: string[];
        };
        Relationships: [
          {
            foreignKeyName: "post_image_pool_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
        ];
      };
      post_private_assets: {
        Row: {
          created_at: string;
          image_url: string;
          post_id: string;
        };
        Insert: {
          created_at?: string;
          image_url: string;
          post_id: string;
        };
        Update: {
          created_at?: string;
          image_url?: string;
          post_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "post_private_assets_post_id_fkey";
            columns: ["post_id"];
            isOneToOne: true;
            referencedRelation: "posts";
            referencedColumns: ["id"];
          },
        ];
      };
      posts: {
        Row: {
          caption: string | null;
          character_id: string;
          comment_count: number;
          created_at: string;
          id: string;
          image_url: string;
          is_paid: boolean;
          like_count: number;
          price_tokens: number;
          published_at: string;
          source_event_id: string | null;
        };
        Insert: {
          caption?: string | null;
          character_id: string;
          comment_count?: number;
          created_at?: string;
          id?: string;
          image_url: string;
          is_paid?: boolean;
          like_count?: number;
          price_tokens?: number;
          published_at?: string;
          source_event_id?: string | null;
        };
        Update: {
          caption?: string | null;
          character_id?: string;
          comment_count?: number;
          created_at?: string;
          id?: string;
          image_url?: string;
          is_paid?: boolean;
          like_count?: number;
          price_tokens?: number;
          published_at?: string;
          source_event_id?: string | null;
        };
        Relationships: [
          {
            foreignKeyName: "posts_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "posts_source_event_id_fkey";
            columns: ["source_event_id"];
            isOneToOne: false;
            referencedRelation: "character_events";
            referencedColumns: ["id"];
          },
        ];
      };
      proactive_messages: {
        Row: {
          character_id: string;
          conversation_id: string;
          id: string;
          message_id: string | null;
          meta: Json;
          replied_at: string | null;
          sent_at: string;
          trigger: string;
          trigger_ref: string;
          user_id: string;
        };
        Insert: {
          character_id: string;
          conversation_id: string;
          id?: string;
          message_id?: string | null;
          meta?: Json;
          replied_at?: string | null;
          sent_at?: string;
          trigger: string;
          trigger_ref: string;
          user_id: string;
        };
        Update: {
          character_id?: string;
          conversation_id?: string;
          id?: string;
          message_id?: string | null;
          meta?: Json;
          replied_at?: string | null;
          sent_at?: string;
          trigger?: string;
          trigger_ref?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "proactive_messages_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "proactive_messages_conversation_id_fkey";
            columns: ["conversation_id"];
            isOneToOne: false;
            referencedRelation: "conversations";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "proactive_messages_message_id_fkey";
            columns: ["message_id"];
            isOneToOne: false;
            referencedRelation: "messages";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "proactive_messages_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      proactive_settings: {
        Row: {
          character_id: string | null;
          enabled: boolean;
          id: string;
          quiet_end: number | null;
          quiet_start: number | null;
          updated_at: string;
          user_id: string;
        };
        Insert: {
          character_id?: string | null;
          enabled?: boolean;
          id?: string;
          quiet_end?: number | null;
          quiet_start?: number | null;
          updated_at?: string;
          user_id: string;
        };
        Update: {
          character_id?: string | null;
          enabled?: boolean;
          id?: string;
          quiet_end?: number | null;
          quiet_start?: number | null;
          updated_at?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "proactive_settings_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "proactive_settings_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      profiles: {
        Row: {
          created_at: string;
          deleted_at: string | null;
          display_name: string | null;
          id: string;
        };
        Insert: {
          created_at?: string;
          deleted_at?: string | null;
          display_name?: string | null;
          id: string;
        };
        Update: {
          created_at?: string;
          deleted_at?: string | null;
          display_name?: string | null;
          id?: string;
        };
        Relationships: [];
      };
      promises: {
        Row: {
          cancelled_at: string | null;
          character_id: string;
          completed_at: string | null;
          content: string;
          created_at: string;
          due_at: string | null;
          due_precision: string;
          event_id: string | null;
          id: string;
          mentioned_at: string | null;
          source_memory_id: string | null;
          source_message_id: string | null;
          status: string;
          updated_at: string;
          user_id: string;
        };
        Insert: {
          cancelled_at?: string | null;
          character_id: string;
          completed_at?: string | null;
          content: string;
          created_at?: string;
          due_at?: string | null;
          due_precision?: string;
          event_id?: string | null;
          id?: string;
          mentioned_at?: string | null;
          source_memory_id?: string | null;
          source_message_id?: string | null;
          status?: string;
          updated_at?: string;
          user_id: string;
        };
        Update: {
          cancelled_at?: string | null;
          character_id?: string;
          completed_at?: string | null;
          content?: string;
          created_at?: string;
          due_at?: string | null;
          due_precision?: string;
          event_id?: string | null;
          id?: string;
          mentioned_at?: string | null;
          source_memory_id?: string | null;
          source_message_id?: string | null;
          status?: string;
          updated_at?: string;
          user_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "promises_character_id_fkey";
            columns: ["character_id"];
            isOneToOne: false;
            referencedRelation: "characters";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "promises_event_id_fkey";
            columns: ["event_id"];
            isOneToOne: false;
            referencedRelation: "character_events";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "promises_source_memory_id_fkey";
            columns: ["source_memory_id"];
            isOneToOne: false;
            referencedRelation: "memories";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "promises_source_message_id_fkey";
            columns: ["source_message_id"];
            isOneToOne: false;
            referencedRelation: "messages";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "promises_user_id_fkey";
            columns: ["user_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
    };
    Views: {
      [_ in never]: never;
    };
    Functions: {
      list_dm_threads: {
        Args: never;
        Returns: {
          character_avatar_url: string;
          character_handle: string;
          character_id: string;
          character_name: string;
          conversation_id: string;
          last_message_at: string;
          last_message_body: string;
          last_message_sender_type: string;
          unread_count: number;
        }[];
      };
      mark_conversation_read: {
        Args: { p_conversation_id: string };
        Returns: undefined;
      };
    };
    Enums: {
      [_ in never]: never;
    };
    CompositeTypes: {
      [_ in never]: never;
    };
  };
};

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">;

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">];

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R;
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] & DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R;
      }
      ? R
      : never
    : never;

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I;
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I;
      }
      ? I
      : never
    : never;

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U;
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U;
      }
      ? U
      : never
    : never;

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    keyof DefaultSchema["Enums"] | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never;

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    keyof DefaultSchema["CompositeTypes"] | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never;

export const Constants = {
  public: {
    Enums: {},
  },
} as const;
