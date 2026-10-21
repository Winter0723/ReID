export const API = "/api/v1/scenic/visitor";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch(API + path, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      headers: { "X-Visitor-Request": "1", ...options.headers },
    });
    const data =
      response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok)
      throw new ApiError(
        response.status,
        typeof data?.detail === "string"
          ? data.detail
          : "服务暂不可用，请稍后重试",
      );
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new Error("网络连接中断，请检查网络后重试");
  } finally {
    clearTimeout(timer);
  }
}
export const post = <T>(path: string) => api<T>(path, { method: "POST" });
export interface Site {
  id: number;
  name: string;
  description: string;
  consent_text: string;
  consent_version: string;
  price_cents: number;
}
export interface Visit {
  selection_available?: boolean;
  can_retry?: boolean;
  render_progress?: {
    orientation: string;
    status: string;
    progress: number;
    message: string;
  }[];
  payment_provider?: string;
  download_urls?: Record<string, string>;
  delivery_page_url?: string;
  id: number;
  project_id: number;
  name: string;
  created_at: string;
  checked_out_at: string | null;
  stage: string;
  message: string;
  price_cents: number;
  video_available: boolean;
  expired: boolean;
  order: null | {
    id: number;
    order_no: string;
    payment_provider?: string;
    status: string;
    amount_cents: number;
    expires_at: string;
    claimed: boolean;
  };
}
export const money = (cents: number) => `¥${(cents / 100).toFixed(2)}`;
export const date = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

export type Orientation = "landscape" | "portrait";
export interface MatchedSegment {
  id: number;
  camera_name: string;
  similarity: number;
  start_seconds: number;
  end_seconds: number;
  included: boolean;
  thumbnail_url: string;
  preview_url: string;
}
export interface StoryClip {
  id: string;
  kind: "video" | "image";
  camera_name: string;
  start_seconds: number;
  end_seconds: number;
  available_start_seconds: number;
  available_end_seconds: number;
  included: boolean;
  locked: boolean;
  keep_audio: boolean;
  has_audio: boolean;
  focus_x: number;
  focus_y: number;
  crop_mode: "fit" | "focus" | "track";
  title: string;
  thumbnail_url: string;
  preview_url: string;
}
export interface StoryConfig {
  template_id: string;
  title: string;
  brand: string;
  music_id: string;
  music_volume: number;
  original_volume: number;
  landscape_seconds: number;
  portrait_seconds: number;
  use_tracking: boolean;
}
export interface StoryDraft {
  id: number;
  revision: number;
  data: StoryConfig & {
    sources: StoryClip[];
    variants: Record<Orientation, StoryClip[]>;
    cover_clip_id: string;
    warnings: string[];
  };
}
export interface VisitorEditorData {
  editable: boolean;
  match_revision: string;
  segments: MatchedSegment[];
  draft: StoryDraft | null;
  catalog: {
    enabled: boolean;
    tracking_enabled: boolean;
    templates: { id: string; name: string; description?: string }[];
    music: { id: string; name: string; preview_url?: string }[];
  };
}
