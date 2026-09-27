/** TanStack Query hooks. Keys are centralized here so invalidation after jobs is consistent. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Health, type ImageItem, type Job } from "./client";

export const keys = {
  health: ["health"] as const,
  vram: ["vram"] as const,
  characters: (params: Record<string, string | boolean | undefined>) => ["characters", params] as const,
  character: (id: string) => ["character", id] as const,
  versions: (id: string) => ["versions", id] as const,
  images: (params: Record<string, string | number | boolean | undefined>) => ["images", params] as const,
  image: (id: string) => ["image", id] as const,
  job: (id: string) => ["job", id] as const,
  jobs: ["jobs"] as const,
  scenes: ["scenes"] as const,
  styles: ["styles"] as const,
  vramTable: ["vramTable"] as const,
  audit: (limit: number) => ["audit", limit] as const,
  compliance: ["compliance"] as const,
  models: ["models"] as const,
  workflow: (method: string) => ["workflow", method] as const,
};

export const JOB_POLL_MS = 1000;

export function isFinished(job: Pick<Job, "status"> | undefined): boolean {
  return !!job && (job.status === "done" || job.status === "error" || job.status === "canceled");
}

export function useHealth(enabled = true) {
  return useQuery({
    queryKey: keys.health,
    queryFn: async () => {
      const r = await api.GET("/api/health");
      // 503 still carries a body we want to show
      if (r.response.status === 503 && r.error) return r.error as unknown as Health;
      return unwrap(r);
    },
    enabled,
    refetchInterval: 15_000,
  });
}

export function useVram(enabled = true) {
  return useQuery({
    queryKey: keys.vram,
    queryFn: async () => unwrap(await api.GET("/api/system/vram")),
    enabled,
    refetchInterval: 5_000,
    retry: false,
  });
}

export function useCharacters(params: { q?: string; tag?: string; sort?: "recent" | "name" | "generations"; include_drafts?: boolean } = {}) {
  return useQuery({
    queryKey: keys.characters(params),
    queryFn: async () => unwrap(await api.GET("/api/characters", { params: { query: params } })).items,
  });
}

export function useCharacter(id: string | undefined) {
  return useQuery({
    queryKey: keys.character(id ?? ""),
    queryFn: async () => unwrap(await api.GET("/api/characters/{character_id}", { params: { path: { character_id: id! } } })),
    enabled: !!id,
  });
}

export function useVersions(id: string | undefined) {
  return useQuery({
    queryKey: keys.versions(id ?? ""),
    queryFn: async () => unwrap(await api.GET("/api/characters/{character_id}/versions", { params: { path: { character_id: id! } } })),
    enabled: !!id,
  });
}

export interface ImageQuery {
  character_id?: string;
  kind?: "generated" | "draft" | "verify" | "upload";
  job_id?: string;
  from?: string;
  to?: string;
  min_similarity?: number;
  favorite?: boolean;
  tag?: string;
  q?: string;
  seed?: number;
  face_method?: "pulid" | "faceid" | "instantid";
  limit?: number;
  offset?: number;
}

export function useImages(params: ImageQuery, enabled = true) {
  return useQuery({
    queryKey: keys.images(params as Record<string, string | number | boolean | undefined>),
    queryFn: async () => unwrap(await api.GET("/api/images", { params: { query: params } })),
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useImage(id: string | undefined) {
  return useQuery({
    queryKey: keys.image(id ?? ""),
    queryFn: async () => unwrap(await api.GET("/api/images/{image_id}", { params: { path: { image_id: id! } } })),
    enabled: !!id,
  });
}

export function useJob(id: string | undefined) {
  return useQuery({
    queryKey: keys.job(id ?? ""),
    queryFn: async () => unwrap(await api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id! } } })),
    enabled: !!id,
    refetchInterval: (query) => (isFinished(query.state.data) ? false : JOB_POLL_MS),
  });
}

export function useJobs(enabled = true) {
  return useQuery({
    queryKey: keys.jobs,
    queryFn: async () => unwrap(await api.GET("/api/jobs", { params: { query: { limit: 30 } } })).items,
    enabled,
    refetchInterval: JOB_POLL_MS,
    retry: false,
  });
}

export function useScenes() {
  return useQuery({
    queryKey: keys.scenes,
    queryFn: async () => unwrap(await api.GET("/api/presets/scenes")).items,
    staleTime: 60_000,
  });
}

export function useStyles() {
  return useQuery({
    queryKey: keys.styles,
    queryFn: async () => unwrap(await api.GET("/api/presets/styles")),
    staleTime: 600_000,
  });
}

export function useVramTable() {
  return useQuery({ queryKey: keys.vramTable, queryFn: async () => unwrap(await api.GET("/api/system/vram-table")) });
}

export function useAudit(limit: number) {
  return useQuery({
    queryKey: keys.audit(limit),
    queryFn: async () => unwrap(await api.GET("/api/audit", { params: { query: { limit } } })),
  });
}

export function useCompliance() {
  return useQuery({
    queryKey: keys.compliance,
    queryFn: async () => unwrap(await api.GET("/api/system/compliance")),
    staleTime: Infinity,
  });
}

export function useWorkflowJson(method: string | undefined) {
  return useQuery({
    queryKey: keys.workflow(method ?? ""),
    queryFn: async () => unwrap(await api.GET("/api/system/workflows/{method}", { params: { path: { method: method! } } })),
    enabled: !!method,
    staleTime: Infinity,
  });
}

/** Invalidate everything a finished job can have changed. */
export function useInvalidateAfterJob() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["images"] });
    void qc.invalidateQueries({ queryKey: ["characters"] });
    void qc.invalidateQueries({ queryKey: ["character"] });
    void qc.invalidateQueries({ queryKey: keys.vram });
  };
}

export function usePatchImage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: { favorite?: boolean | null; rating?: number | null; tags?: string[] | null } }) =>
      unwrap(await api.PATCH("/api/images/{image_id}", { params: { path: { image_id: id } }, body })),
    onSuccess: (data) => {
      qc.setQueryData(keys.image(data.id), data);
      void qc.invalidateQueries({ queryKey: ["images"] });
    },
  });
}

export function upsertImageInLists(items: ImageItem[] | undefined, updated: ImageItem): ImageItem[] | undefined {
  return items?.map((i) => (i.id === updated.id ? { ...i, ...updated } : i));
}
