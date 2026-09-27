/**
 * Client state (Zustand). Persisted keys: apiKey, blurDefault, similarity thresholds, recent characters, tray.
 * The API key lives in localStorage because the app is local-only (README explains what to change before exposing it).
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface SimilarityThresholds {
  good: number;
  acceptable: number;
}

export interface PinnedParams {
  characterId: string;
  prompt: string;
  negative_prompt: string | null;
  scene_ids: string[];
  width: number | null;
  height: number | null;
  upscale: number | null;
  face_detailer: boolean | null;
  steps: number | null;
  cfg: number | null;
  sampler_name: string | null;
  scheduler: string | null;
  seed: number;
}

interface UiState {
  apiKey: string;
  unauthorized: boolean;
  blurDefault: boolean;
  revealed: Record<string, true>;
  compareTray: string[];
  recentCharacters: string[];
  similarity: SimilarityThresholds;
  vramWarnPercent: number;
  pinnedParams: PinnedParams | null;
  setApiKey: (key: string) => void;
  setUnauthorized: (value: boolean) => void;
  setBlurDefault: (value: boolean) => void;
  reveal: (imageId: string) => void;
  toggleTray: (imageId: string) => void;
  clearTray: () => void;
  touchCharacter: (characterId: string) => void;
  setSimilarity: (value: SimilarityThresholds) => void;
  setVramWarnPercent: (value: number) => void;
  setPinnedParams: (params: PinnedParams | null) => void;
}

const COOKIE = "psk";

function syncCookie(key: string): void {
  if (typeof document === "undefined") return;
  if (key) {
    document.cookie = `${COOKIE}=${encodeURIComponent(key)}; path=/; SameSite=Strict; max-age=31536000`;
  } else {
    document.cookie = `${COOKIE}=; path=/; SameSite=Strict; max-age=0`;
  }
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      apiKey: "",
      unauthorized: false,
      blurDefault: true,
      revealed: {},
      compareTray: [],
      recentCharacters: [],
      similarity: { good: 0.75, acceptable: 0.6 },
      vramWarnPercent: 85,
      pinnedParams: null,
      setApiKey: (key) => {
        syncCookie(key);
        set({ apiKey: key, unauthorized: false });
      },
      setUnauthorized: (value) => set({ unauthorized: value }),
      setBlurDefault: (value) => set({ blurDefault: value, revealed: {} }),
      reveal: (imageId) => set({ revealed: { ...get().revealed, [imageId]: true } }),
      toggleTray: (imageId) => {
        const tray = get().compareTray;
        set({ compareTray: tray.includes(imageId) ? tray.filter((i) => i !== imageId) : [...tray, imageId].slice(-12) });
      },
      clearTray: () => set({ compareTray: [] }),
      touchCharacter: (characterId) => {
        const recent = [characterId, ...get().recentCharacters.filter((i) => i !== characterId)].slice(0, 6);
        set({ recentCharacters: recent });
      },
      setSimilarity: (value) => set({ similarity: value }),
      setVramWarnPercent: (value) => set({ vramWarnPercent: value }),
      setPinnedParams: (params) => set({ pinnedParams: params }),
    }),
    {
      name: "portrait-studio-ui",
      partialize: (s) => ({
        apiKey: s.apiKey,
        blurDefault: s.blurDefault,
        compareTray: s.compareTray,
        recentCharacters: s.recentCharacters,
        similarity: s.similarity,
        vramWarnPercent: s.vramWarnPercent,
      }),
      onRehydrateStorage: () => (state) => {
        if (state?.apiKey) syncCookie(state.apiKey);
      },
    },
  ),
);
