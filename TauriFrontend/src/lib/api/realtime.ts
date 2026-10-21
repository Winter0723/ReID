import { apiRequest, getApiBaseUrl } from "./http";

export type Crop = [number, number, number, number];
export interface RealtimeInput { id: string; kind: string; preview?: string; width?: number; height?: number; boxes?: { box: Crop; confidence: number }[] }
export type RealtimeSource = { media_id: number } | { input_id: string } | { camera: string };
export interface RealtimeFrame {
  session_id: string; sequence: number; source_time: number; width: number; height: number;
  target_state: string; processing_ms: number; fps: number; sent_at: number; jpeg: string;
  people: { track_id: number; target: boolean; similarity: number; detection_confidence: number }[];
}
export interface RealtimeSession { id: string; status: string; error: string; epoch: number }
const base = (project: number) => `/api/v1/projects/${project}/realtime`;
export const realtimeApi = {
  preview(project: number, media: number) {
    return apiRequest<{ preview: string }>(`${base(project)}/media/${media}/preview`, { authScope: "core" });
  },
  cameras(project: number) {
    return apiRequest<{ devices: { source: string; label: string }[] }>(`${base(project)}/cameras`, { authScope: "core" });
  },
  upload(project: number, file: File, kind: "image" | "video") {
    const body = new FormData(); body.append("file", file);
    return apiRequest<RealtimeInput>(`${base(project)}/inputs`, { method: "POST", query: { kind }, body, authScope: "core" });
  },
  remove(project: number, id: string) {
    return apiRequest(`${base(project)}/inputs/${id}`, { method: "DELETE", authScope: "core" });
  },
  start(project: number, reference_id: string, crop: Crop, source: RealtimeSource, show_others: boolean) {
    return apiRequest<RealtimeSession>(`${base(project)}/sessions`, { method: "POST", authScope: "core", json: { reference_id, crop, source, show_others } });
  },
  control(project: number, id: string, action: "pause" | "resume" | "stop") {
    return apiRequest<RealtimeSession>(`${base(project)}/sessions/${id}/control`, { method: "POST", authScope: "core", json: { action } });
  },
  ticket(project: number, id: string) {
    return apiRequest<{ ticket: string }>(`${base(project)}/sessions/${id}/stream-ticket`, { method: "POST", authScope: "core" });
  },
  streamUrl(project: number, id: string, ticket: string) {
    const direct = Boolean(window.__TAURI_INTERNALS__) || Boolean(import.meta.env.VITE_MYREID_API_BASE_URL);
    const url = new URL(`${base(project)}/sessions/${id}/stream`, direct ? getApiBaseUrl() : location.origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("ticket", ticket);
    return url.href;
  },
};
