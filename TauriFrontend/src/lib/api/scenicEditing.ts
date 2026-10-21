import { apiRequest } from "./http";

export type VlogOrientation = "landscape" | "portrait";
export interface EditingClip {
  id: string;
  segment_id?: number;
  media_id: number;
  object_key: string;
  source_sha256: string;
  kind: "video" | "image";
  start_seconds: number;
  end_seconds: number;
  camera_name?: string;
  recorded_at?: string;
  thumbnail_url?: string;
  original_media_url?: string;
  width?: number;
  height?: number;
  rotation?: number;
  duration_seconds?: number;
  quality?: number | Record<string, unknown>;
  locked: boolean;
  included: boolean;
  keep_audio: boolean;
  focus_x: number;
  focus_y: number;
  crop_mode: "fit" | "focus" | "track";
  [key: string]: unknown;
}
export interface EditingDraft {
  schema_version: 1;
  template_id: string;
  template_version: string;
  title: string;
  brand: string;
  music_id: string;
  music_volume: number;
  original_volume: number;
  landscape_seconds: number;
  portrait_seconds: number;
  sources: EditingClip[];
  variants: Record<VlogOrientation, EditingClip[]>;
  warnings: string[];
  cover_clip_id?: string;
  [key: string]: unknown;
}
export interface DraftRecord {
  id: number;
  session_id: number;
  revision: number;
  data: EditingDraft;
  created_at?: string;
}
export interface EditingCatalog {
  enabled: boolean;
  tracking_enabled?: boolean;
  templates: Array<{ id: string; name: string; version: string; description: string; music_id: string; min_clip_seconds: number; max_clip_seconds: number; color: string; transition_seconds?: number }>;
  music: Array<{
    id: string;
    name: string;
    style?: string;
    description?: string;
    source?: string;
    source_url?: string;
    author?: string;
    license?: string;
    license_url?: string;
    usage?: string;
    preview_url?: string;
    duration_seconds?: number;
    origin?: "download" | "generated";
    url?: string;
  }>;
}
export interface AutoDraftOptions {
  template_id: string;
  title: string;
  brand: string;
  music_id: string;
  music_volume: number;
  original_volume: number;
  landscape_seconds: number;
  portrait_seconds: number;
  use_tracking: boolean;
  previous_draft_id?: number;
}
const base = "/api/v1/scenic";
export const scenicEditingApi = {
  metadata: (projectId: number, mediaId: number) => apiRequest<{ recorded_at: string | null; location: string }>(`${base}/projects/${projectId}/media/${mediaId}/editing-metadata`, { authScope: "scenic" }),
  saveMetadata: (projectId: number, mediaId: number, data: { recorded_at: string | null; location: string }) => apiRequest<unknown>(`${base}/projects/${projectId}/media/${mediaId}/editing-metadata`, { authScope: "scenic", method: "PATCH", json: data }),
  catalog: () => apiRequest<EditingCatalog>(`${base}/editing/catalog`, { authScope: "scenic" }),
  drafts: (sessionId: number) => apiRequest<{ drafts: DraftRecord[] }>(`${base}/tourists/${sessionId}/drafts`, { authScope: "scenic" }),
  auto: (sessionId: number, options: AutoDraftOptions) => apiRequest<DraftRecord>(`${base}/tourists/${sessionId}/drafts/auto`, { authScope: "scenic", method: "POST", json: options }),
  save: (sessionId: number, record: DraftRecord) => apiRequest<DraftRecord>(`${base}/tourists/${sessionId}/drafts/${record.id}`, { authScope: "scenic", method: "PUT", json: { revision: record.revision, data: record.data } }),
  retry: (vlogId: number, variantId: number) => apiRequest<unknown>(`${base}/vlogs/${vlogId}/variants/${variantId}/retry`, { authScope: "scenic", method: "POST" }),
};
