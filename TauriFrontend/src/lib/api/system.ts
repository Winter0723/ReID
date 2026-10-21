import { apiRequest, type AuthScope } from "./http";

export interface ReadyzPayload {
  status: string;
  clip_loaded?: boolean;
  yolo_loaded?: boolean;
  queue_ready?: boolean;
  queue_mode?: "redis" | "local" | "unavailable" | string;
  degraded_reasons?: string[];
  device?: string;
  models?: Record<string, unknown>;
  database?: Record<string, unknown>;
  vector_store?: Record<string, unknown>;
  disk?: Record<string, unknown>;
}

export interface CameraDevice {
  label?: string;
  name?: string;
  source?: string | number;
  type?: string;
  description?: string;
  width?: number;
  height?: number;
}

export interface MetricsPayload {
  projects?: number;
  media_files?: number;
  indexed_targets?: number;
  jobs?: Record<string, unknown>;
  queue?: Record<string, unknown>;
  models?: Record<string, unknown>;
  disk?: Record<string, unknown>;
  errors?: Record<string, unknown> | null;
}

export const systemApi = {
  readyz(signal?: AbortSignal): Promise<ReadyzPayload> {
    return apiRequest<ReadyzPayload>("/api/v1/readyz", { signal });
  },

  cameras(scope: AuthScope = "core", refresh = false, signal?: AbortSignal): Promise<{ status: string; devices: CameraDevice[]; message: string }> {
    return apiRequest<{ status: string; devices: CameraDevice[]; message: string }>("/api/v1/cameras", {
      authScope: scope,
      query: refresh ? { refresh: true } : undefined,
      signal,
    });
  },

  scenicCameras(refresh = false, signal?: AbortSignal): Promise<{ status: string; devices: CameraDevice[]; message: string }> {
    return apiRequest<{ status: string; devices: CameraDevice[]; message: string }>("/api/v1/scenic/cameras", {
      authScope: "scenic",
      query: refresh ? { refresh: true } : undefined,
      signal,
    });
  },

  metrics(signal?: AbortSignal): Promise<MetricsPayload> {
    return apiRequest<MetricsPayload>("/api/v1/metrics", { authScope: "core", signal });
  },

  observability(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return apiRequest<Record<string, unknown>>("/api/v1/observability", { authScope: "core", signal });
  },
};
