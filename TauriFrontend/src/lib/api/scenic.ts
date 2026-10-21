import { apiRequest } from "./http";
import type { SearchHistoryItem } from "./search";

export type ScenicWorkQueue = "needs_review" | "needs_vlog" | "failed" | "awaiting_payment" | "ready_delivery";
export interface ScenicPagination { page: number; page_size: number; total: number; total_pages: number }
export interface ScenicListQuery {
  page: number;
  page_size: number;
  q: string;
  date_from: string;
  date_to: string;
}
export interface ScenicTouristQuery extends ScenicListQuery { status: string; work_queue: ScenicWorkQueue | "" }
export interface ScenicTouristList {
  status?: string;
  tourists: TouristSession[];
  pagination?: ScenicPagination;
  work_queue_counts?: Partial<Record<ScenicWorkQueue, number>>;
}
export interface ScenicHistoryQuery extends ScenicListQuery { query_type: string }
export interface ScenicAuditQuery extends ScenicListQuery { action: string }


export interface ScenicDashboard {
  status: string;
  project_id: number;
  today_sessions?: number;
  active_sessions?: number;
  matched_sessions?: number;
  camera_points?: number;
  active_camera_points?: number;
  camera_status?: Record<string, unknown>;
  camera_wall?: ScenicCameraWallItem[];
  pending_vlogs?: number;
  ready_vlogs?: number;
  pending_orders?: number;
  paid_orders?: number;
  paid_revenue_cents?: number;
  recent_sessions?: TouristSession[];
}

export interface ScenicCameraPoint {
  id: number;
  name: string;
  camera_source: string;
  camera_id?: string;
  location?: string;
  role: "entry" | "play" | "exit" | string;
  is_active?: boolean;
}

export interface ScenicCameraWallItem {
  id?: number;
  point_id?: number;
  name?: string;
  role?: string;
  location?: string;
  camera_source?: string;
  camera_id?: string;
  is_active?: boolean;
  running?: boolean;
  monitor_running?: boolean;
  capture_fps?: number;
  preview_fps?: number;
  preview_width?: number;
  preview_height?: number;
  latest_frame_url?: string;
  latest_frame_version?: number;
  indexed_targets?: number;
  message?: string;
  recording_url?: string;
  recording_filename?: string;
  recording_ready?: boolean;
}

export interface TouristSegment {
  id: number;
  target_id?: number;
  media_id?: number;
  camera_name?: string;
  camera_id?: string;
  timestamp?: string;
  start_seconds?: number;
  end_seconds?: number;
  duration_seconds?: number;
  similarity?: number;
  thumbnail_url?: string;
  original_media_url?: string;
  included?: boolean;
  sort_order?: number;
}

export interface ScenicSnapshot {
  status: string;
  snapshot_url: string;
  snapshot_filename: string;
  snapshot_object_key: string;
  point_id?: number;
}

export interface TouristSession {
  registration_source?: "visitor" | "staff";
  production_request_id?: number | null;
  id: number;
  ticket_code: string;
  visitor_label?: string;
  contact_hint?: string;
  status?: string;
  entry_image_url?: string;
  work_queues?: ScenicWorkQueue[];
  segment_count?: number;
  best_similarity?: number;
  created_at?: string;
  checked_out_at?: string;
  entry_target_box?: [number, number, number, number] | null;
  segments?: TouristSegment[];
  vlog_jobs?: ScenicVlogJob[];
  orders?: ScenicOrder[];
}

export interface ScenicVlogJob {
  id: number;
  status: string;
  processing_job_id?: string;
  progress?: number;
  preview_url?: string;
  full_url?: string;
  preview_filename?: string;
  full_filename?: string;
  segment_count?: number;
  duration_seconds?: number;
  message?: string;
  error?: string;
  created_at?: string;
  updated_at?: string;
  draft_id?: number;
  draft_revision?: number;
  template_id?: string;
  variants?: ScenicVlogVariant[];
}

export interface ScenicVlogVariant {
  id: number;
  orientation: "landscape" | "portrait";
  status: string;
  progress?: number;
  message?: string;
  error?: string;
  duration_seconds?: number;
  preview_url?: string;
  full_url?: string;
  draft_preview_url?: string;
  cover_url?: string;
}

export interface ScenicOrder {
  id: number;
  order_no?: string;
  status: string;
  amount_cents?: number;
  currency?: string;
  payment_method?: string;
  payment_provider?: string;
  provider_trade_no?: string;
  provider_trade_state?: string;
  payment_code_url?: string;
  payment_qr_url?: string;
  payment_error?: string;
  payment_expires_at?: string;
  last_payment_sync_at?: string;
  notify_event_id?: string;
  delivery_token?: string;
  delivery_url?: string;
  payment_code?: string;
  payment_qr_text?: string;
  preview_url?: string;
  full_url?: string;
  created_at?: string;
  paid_at?: string;
  delivered_at?: string;
  claimed_at?: string;
  vlog_job_id?: number;
  delivery_page_url?: string;
  delivery_qr_url?: string;
  variants?: ScenicVlogVariant[];
}

export const scenicApi = {
  dashboard(projectId: number, signal?: AbortSignal): Promise<ScenicDashboard> {
    return apiRequest<ScenicDashboard>(`/api/v1/scenic/projects/${projectId}/dashboard`, { authScope: "scenic", signal });
  },

  cameraPoints(projectId: number, signal?: AbortSignal): Promise<{ status: string; camera_points: ScenicCameraPoint[] }> {
    return apiRequest<{ status: string; camera_points: ScenicCameraPoint[] }>(`/api/v1/scenic/projects/${projectId}/camera-points`, {
      authScope: "scenic",
      signal,
    });
  },

  cameraWall(projectId: number, light = false, signal?: AbortSignal): Promise<{ status: string; camera_wall: ScenicCameraWallItem[]; active_count: number; running_count: number }> {
    return apiRequest<{ status: string; camera_wall: ScenicCameraWallItem[]; active_count: number; running_count: number }>(
      `/api/v1/scenic/projects/${projectId}/camera-wall`,
      { authScope: "scenic", query: light ? { light } : undefined, signal },
    );
  },

  prepareCameraPreview(projectId: number, pointId: number, signal?: AbortSignal): Promise<{ status: string; camera: ScenicCameraWallItem }> {
    return apiRequest<{ status: string; camera: ScenicCameraWallItem }>(
      `/api/v1/scenic/projects/${projectId}/camera-points/${pointId}/preview`,
      { method: "POST", authScope: "scenic", signal },
    );
  },

  discoverCameraPoints(projectId: number, signal?: AbortSignal): Promise<{ status: string; created: number; camera_points: ScenicCameraPoint[] }> {
    return apiRequest<{ status: string; created: number; camera_points: ScenicCameraPoint[] }>(
      `/api/v1/scenic/projects/${projectId}/camera-points/discover`,
      { method: "POST", authScope: "scenic", signal },
    );
  },

  createCameraPoint(projectId: number, payload: { name: string; camera_source: string; role: string; location?: string }): Promise<ScenicCameraPoint> {
    return apiRequest<ScenicCameraPoint>(`/api/v1/scenic/projects/${projectId}/camera-points`, {
      method: "POST",
      authScope: "scenic",
      json: payload,
    });
  },

  updateCameraPoint(pointId: number, payload: Partial<Pick<ScenicCameraPoint, "name" | "camera_source" | "camera_id" | "role" | "location" | "is_active">>): Promise<ScenicCameraPoint> {
    return apiRequest<ScenicCameraPoint>(`/api/v1/scenic/camera-points/${pointId}`, {
      method: "PATCH",
      authScope: "scenic",
      json: payload,
    });
  },

  startCameraWall(projectId: number, cameraSource = "0", signal?: AbortSignal): Promise<unknown> {
    return apiRequest<unknown>(`/api/v1/scenic/projects/${projectId}/camera-points/start-all`, {
      method: "POST",
      signal,
      authScope: "scenic",
      json: { camera_source: cameraSource, sample_interval_seconds: 1, min_confidence: 0.35 },
    });
  },

  stopCameraWall(projectId: number): Promise<unknown> {
    return apiRequest<unknown>(`/api/v1/scenic/projects/${projectId}/camera-points/stop-all`, { method: "POST", authScope: "scenic" });
  },

  snapshot(projectId: number, pointId?: number): Promise<ScenicSnapshot> {
    return apiRequest<ScenicSnapshot>(`/api/v1/scenic/projects/${projectId}/camera/snapshot`, { method: "POST", authScope: "scenic", query: { point_id: pointId } });
  },

  registerTourist(projectId: number, source: File | ScenicSnapshot, payload: { ticket_code: string; visitor_label: string; contact_hint: string; consent_granted: boolean }): Promise<TouristSession> {
    const form = new FormData();
    if (source instanceof File) {
      form.append("file", source);
    } else {
      form.append("snapshot_object_key", source.snapshot_object_key);
    }
    form.append("ticket_code", payload.ticket_code);
    form.append("visitor_label", payload.visitor_label);
    form.append("contact_hint", payload.contact_hint);
    form.append("consent_granted", String(payload.consent_granted));
    return apiRequest<TouristSession>(`/api/v1/scenic/projects/${projectId}/tourists`, {
      method: "POST",
      authScope: "scenic",
      body: form,
    });
  },

  uploadSimulationVideo(projectId: number, pointId: number, file: File): Promise<{ status: string; media_id: number; job_id: string }> {
    const form = new FormData();
    form.append("file", file);
    return apiRequest<{ status: string; media_id: number; job_id: string }>(`/api/v1/scenic/projects/${projectId}/camera-points/${pointId}/simulate-video`, {
      method: "POST",
      authScope: "scenic",
      body: form,
    });
  },

  tourists(projectId: number, signal?: AbortSignal, query?: ScenicTouristQuery): Promise<ScenicTouristList> {
    return apiRequest<ScenicTouristList>(`/api/v1/scenic/projects/${projectId}/tourists`, { authScope: "scenic", signal, query: query ? { ...query } : undefined });
  },

  history(projectId: number, query: ScenicHistoryQuery, signal?: AbortSignal): Promise<{ history: SearchHistoryItem[]; pagination?: ScenicPagination }> {
    return apiRequest("/api/v1/scenic/history", { authScope: "scenic", signal, query: { ...query, project_id: projectId } });
  },

  audit(projectId: number, query: ScenicAuditQuery, signal?: AbortSignal): Promise<{ audit: Record<string, unknown>[]; pagination?: ScenicPagination }> {
    return apiRequest(`/api/v1/scenic/projects/${projectId}/audit`, { authScope: "scenic", signal, query: { ...query } });
  },

  tourist(sessionId: number, signal?: AbortSignal): Promise<TouristSession> {
    return apiRequest<TouristSession>(`/api/v1/scenic/tourists/${sessionId}`, { authScope: "scenic", signal });
  },

  matchTourist(sessionId: number, minSimilarity: number): Promise<{ status: string; tourist: TouristSession; segments: TouristSegment[] }> {
    return apiRequest<{ status: string; tourist: TouristSession; segments: TouristSegment[] }>(`/api/v1/scenic/tourists/${sessionId}/match`, {
      method: "POST",
      authScope: "scenic",
      json: { top_k: 80, min_similarity: minSimilarity, clip_padding_seconds: 2, min_clip_seconds: 3, max_clip_seconds: 8 },
    });
  },

  updateSegment(sessionId: number, segmentId: number, included: boolean, sortOrder: number): Promise<TouristSegment> {
    return apiRequest<TouristSegment>(`/api/v1/scenic/tourists/${sessionId}/segments/${segmentId}`, {
      method: "PATCH",
      authScope: "scenic",
      json: { included, sort_order: sortOrder },
    });
  },

  updateSegments(sessionId: number, segmentIds: number[], included: boolean): Promise<{ status: string; updated: number; segments: TouristSegment[] }> {
    return apiRequest<{ status: string; updated: number; segments: TouristSegment[] }>(`/api/v1/scenic/tourists/${sessionId}/segments`, {
      method: "PATCH",
      authScope: "scenic",
      json: { segment_ids: segmentIds, included },
    });
  },

  checkout(sessionId: number): Promise<TouristSession> {
    return apiRequest<TouristSession>(`/api/v1/scenic/tourists/${sessionId}/checkout`, { method: "POST", authScope: "scenic" });
  },

  setReference(sessionId: number, box: number[] | null): Promise<TouristSession> {
    return apiRequest<TouristSession>(`/api/v1/scenic/tourists/${sessionId}/reference`, { method: "PATCH", authScope: "scenic", json: { entry_target_box: box } });
  },

  generateVlog(sessionId: number, draft?: { draft_id: number; revision: number }): Promise<{ status: string; job_id: string; vlog: ScenicVlogJob }> {
    return apiRequest<{ status: string; job_id: string; vlog: ScenicVlogJob }>(`/api/v1/scenic/tourists/${sessionId}/vlog`, {
      method: "POST",
      authScope: "scenic",
      json: draft,
    });
  },

  createOrder(sessionId: number, amountCents: number, vlogJobId?: number): Promise<ScenicOrder> {
    return apiRequest<ScenicOrder>(`/api/v1/scenic/tourists/${sessionId}/orders`, {
      method: "POST",
      authScope: "scenic",
      json: { amount_cents: amountCents, currency: "CNY", payment_method: "manual", vlog_job_id: vlogJobId },
    });
  },

  createWechatOrder(sessionId: number, amountCents: number, vlogJobId?: number): Promise<ScenicOrder> {
    return apiRequest<ScenicOrder>(`/api/v1/scenic/tourists/${sessionId}/orders`, {
      method: "POST",
      authScope: "scenic",
      json: { amount_cents: amountCents, currency: "CNY", payment_method: "wechat_native", vlog_job_id: vlogJobId },
    });
  },

  refreshOrderPayment(orderId: number): Promise<ScenicOrder> {
    return apiRequest<ScenicOrder>(`/api/v1/scenic/orders/${orderId}/refresh-payment`, {
      method: "POST",
      authScope: "scenic",
    });
  },

  completeSimulatedPayment(orderId: number, result: "success" | "failed"): Promise<ScenicOrder> {
    return apiRequest<ScenicOrder>(`/api/v1/scenic/orders/${orderId}/simulate-payment-result`, {
      method: "POST",
      authScope: "scenic",
      json: { result },
    });
  },

  markOrderPaid(orderId: number): Promise<ScenicOrder> {
    return apiRequest<ScenicOrder>(`/api/v1/scenic/orders/${orderId}/mark-paid`, {
      method: "POST",
      authScope: "scenic",
    });
  },
};
