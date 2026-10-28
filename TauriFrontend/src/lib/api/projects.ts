import { ApiError, apiRequest, type AuthScope } from "./http";

export interface ReIDModel {
  id: string; name: string; dimension?: number; embedding_space_id?: string;
  available: boolean; unavailable_reason?: string; default?: boolean;
  capabilities?: { image: boolean; text: boolean };
}

export interface Project {
  reid_model_id?: string;
  embedding_space_id?: string;
  reid_model?: ReIDModel;
  id: number;
  name: string;
  description: string;
  project_type: "media" | "camera" | "scenic" | string;
  camera_source?: string;
  owner_username: string;
  created_at: string;
}

export interface ProjectStatus {
  status: string;
  current?: number;
  total?: number;
  indexed_targets?: number;
  media_total?: number;
  indexed_media?: number;
  processing_media?: number;
  failed_media?: number;
  latest_job?: Record<string, unknown> | null;
}

export interface JobItem {
  id: string;
  project_id?: number;
  media_id?: number | null;
  type?: string;
  job_type?: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  progress?: number;
  message?: string;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface CameraStatus {
  running?: boolean;
  monitor_running?: boolean;
  monitor_source?: string;
  monitor_status?: Record<string, unknown>;
  latest_frame_url?: string;
  latest_frame_version?: number;
  indexed_targets?: number;
  message?: string;
  error?: string;
  camera_source?: string;
  recording_url?: string;
  recording_filename?: string;
  recording_media_id?: number;
  recording_ready?: boolean;
  project_type?: string;
}

export interface JobListResponse {
  status: string;
  jobs: JobItem[];
}

export interface ProjectAuditRow {
  id?: number;
  username?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  detail?: string;
  created_at?: string;
  timestamp?: string;
  event?: string;
  details?: string;
  level?: string;
}

export interface ProjectAuditResponse {
  status: string;
  audit: ProjectAuditRow[];
}

export interface MediaPreviewResponse {
  status: string;
  preview_url: string;
  original_url?: string;
  generated?: boolean;
  start_seconds?: number;
  duration_seconds?: number | null;
  media_id?: number;
  message?: string;
}

function projectBase(scope: AuthScope): string {
  return scope === "scenic" ? "/api/v1/scenic/projects" : "/api/v1/projects";
}

function normalizeAuditRow(row: ProjectAuditRow): ProjectAuditRow {
  return {
    ...row,
    timestamp: row.timestamp || row.created_at || "",
    event: row.event || row.action || "audit.event",
    details: row.details || row.detail || [
      row.username ? `user=${row.username}` : "",
      row.resource_type ? `resource=${row.resource_type}:${row.resource_id || ""}` : "",
    ].filter(Boolean).join(" "),
    level: row.level || "INFO",
  };
}

export const projectsApi = {
  async models(scope: AuthScope): Promise<ReIDModel[]> {
    try {
      const rows = await apiRequest<ReIDModel[]>(scope === "scenic" ? "/api/v1/scenic/models" : "/api/v1/models", { authScope: scope });
      if (!Array.isArray(rows)) throw new Error("模型接口返回格式错误，请检查后端版本及 API 代理地址");
      return rows;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        throw new Error("当前后端缺少多模型接口，请运行 scripts/run_production.ps1 更新镜像后重试");
      }
      throw error;
    }
  },
  list(scope: AuthScope, signal?: AbortSignal): Promise<Project[]> {
    return apiRequest<Project[]>(projectBase(scope), { authScope: scope, signal });
  },

  create(scope: AuthScope, payload: { name: string; description: string; project_type?: string; camera_source?: string; reid_model_id?: string }): Promise<Project> {
    return apiRequest<Project>(projectBase(scope), { method: "POST", authScope: scope, json: payload });
  },

  status(scope: AuthScope, projectId: number, signal?: AbortSignal): Promise<ProjectStatus> {
    return apiRequest<ProjectStatus>(`${projectBase(scope)}/${projectId}/status`, { authScope: scope, signal });
  },

  preview(scope: AuthScope, projectId: number, signal?: AbortSignal): Promise<{ previews?: string[] }> {
    return apiRequest<{ previews?: string[] }>(`${projectBase(scope)}/${projectId}/preview`, { authScope: scope, signal });
  },

  jobs(scope: AuthScope, projectId: number, signal?: AbortSignal): Promise<JobItem[]> {
    return apiRequest<JobListResponse>(`${projectBase(scope)}/${projectId}/jobs`, { authScope: scope, signal }).then((response) => response.jobs || []);
  },

  audit(scope: AuthScope, projectId: number, signal?: AbortSignal): Promise<ProjectAuditRow[]> {
    return apiRequest<ProjectAuditResponse>(`${projectBase(scope)}/${projectId}/audit`, { authScope: scope, signal }).then((response) =>
      (response.audit || []).map(normalizeAuditRow),
    );
  },

  rebuildIndex(projectId: number): Promise<{ status: string; job_id: string }> {
    return apiRequest<{ status: string; job_id: string }>("/api/v1/index/rebuild", {
      method: "POST",
      authScope: "core",
      query: { project_id: projectId },
    });
  },

  startCamera(projectId: number, cameraSource = "0"): Promise<CameraStatus> {
    return apiRequest<CameraStatus>(`${projectBase("core")}/${projectId}/camera/start`, {
      method: "POST",
      authScope: "core",
      json: { camera_source: cameraSource || "0", sample_interval_seconds: 1, min_confidence: 0.35 },
    });
  },

  stopCamera(projectId: number): Promise<CameraStatus> {
    return apiRequest<CameraStatus>(`${projectBase("core")}/${projectId}/camera/stop`, { method: "POST", authScope: "core" });
  },

  cameraStatus(projectId: number, light = false, signal?: AbortSignal): Promise<CameraStatus> {
    return apiRequest<CameraStatus>(`${projectBase("core")}/${projectId}/camera/status`, {
      authScope: "core",
      query: light ? { light } : undefined,
      signal,
    });
  },

  videoPreview(
    scope: AuthScope,
    projectId: number,
    mediaId: number,
    options: { start_seconds?: number; duration_seconds?: number; full?: boolean } = {},
  ): Promise<MediaPreviewResponse> {
    return apiRequest<MediaPreviewResponse>(`${projectBase(scope)}/${projectId}/media/${mediaId}/preview-video`, {
      authScope: scope,
        timeoutMs: 480_000,
      query: {
        start_seconds: options.start_seconds,
        duration_seconds: options.duration_seconds,
        full: options.full,
      },
    });
  },
};
