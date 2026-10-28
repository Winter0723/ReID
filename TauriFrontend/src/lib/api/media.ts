import { apiRequest, type AuthScope } from "./http";

export interface MediaTarget {
  id: number;
  media_id: number;
  thumbnail_url: string;
  source_info?: string;
  timestamp?: string;
  video_seek_seconds?: number;
}

export interface MediaItem {
  id: number;
  project_id?: number;
  filename?: string;
  original_filename?: string;
  media_type?: string;
  content_type?: string;
  size_bytes?: number;
  status?: string;
  duplicate_of_media_id?: number | null;
  uploader_username?: string;
  object_url?: string;
  thumbnail_url?: string;
  target_count?: number;
  targets?: MediaTarget[];
  created_at?: string;
}

interface MediaListResponse {
  status: string;
  media: MediaItem[];
}

export interface UploadItem {
  media_id?: number;
  existing_media_id?: number;
  job_id?: string | null;
  filename?: string;
  status: "accepted" | "duplicate" | string;
}

export interface UploadResponse {
  status: "accepted" | "duplicate" | string;
  media_id?: number;
  existing_media_id?: number;
  job_id?: string | null;
  items?: UploadItem[];
}

export const mediaApi = {
  list(projectId: number, scope: AuthScope = "core", signal?: AbortSignal): Promise<MediaItem[]> {
    const path = scope === "scenic" ? `/api/v1/scenic/projects/${projectId}/media` : `/api/v1/projects/${projectId}/media`;
    return apiRequest<MediaListResponse>(path, { authScope: scope, signal }).then((response) => response.media || []);
  },

  upload(projectId: number, files: File[]): Promise<UploadResponse> {
    const form = new FormData();
    const isBatch = files.length > 1;
    files.forEach((file) => form.append(isBatch ? "files" : "file", file));
    return apiRequest<UploadResponse>(`/api/v1/projects/${projectId}/media${isBatch ? "/batch" : ""}`, {
      method: "POST",
      authScope: "core",
      body: form,
    });
  },

  delete(mediaId: number): Promise<{ status: string }> {
    return apiRequest<{ status: string }>(`/api/v1/media/${mediaId}`, { method: "DELETE", authScope: "core" });
  },

  clearProject(projectId: number): Promise<{ status: string }> {
    return apiRequest<{ status: string }>(`/api/v1/projects/${projectId}/media`, { method: "DELETE", authScope: "core" });
  },
};
