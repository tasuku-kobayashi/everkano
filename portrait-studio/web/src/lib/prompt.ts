import type { ScenePreset } from "../api/client";

/** Mirror of the server-side composition (prefix, scene fragments, user prompt) for the preview. */
export function composePrompt(prefix: string, scenes: ScenePreset[], userPrompt: string): string {
  const parts = [prefix.trim(), ...scenes.flatMap((s) => (s.fragments ?? []).map((f) => f.trim())), userPrompt.trim()];
  return parts.filter(Boolean).join(", ");
}

export function parseTags(text: string): string[] {
  return Array.from(new Set(text.split(/[,\s、]+/).map((t) => t.trim()).filter(Boolean)));
}
