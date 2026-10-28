import type { AuthScope } from "./api/http";
import { projectsApi } from "./api/projects";

export interface VideoPreviewSource {
  source: string;
  fallbackSource: string;
  downloadSource: string;
  seekSeconds: number;
  fallbackSeekSeconds: number;
  generated: boolean;
}

export interface VideoPreviewOptions {
  scope: AuthScope;
  projectId: number;
  mediaId?: number;
  fallback: string;
  full?: boolean;
  startSeconds?: number;
  durationSeconds?: number;
  generatedSeekSeconds?: number;
  fallbackSeekSeconds?: number;
}

function seconds(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  if (numeric > 24 * 3600) return fallback;
  return Math.max(0, numeric);
}

export async function resolveVideoPreviewSource(options: VideoPreviewOptions): Promise<VideoPreviewSource> {
  const original = options.fallback || "";
  const full = Boolean(options.full);
  const startSeconds = full ? 0 : seconds(options.startSeconds);
  const fallbackSeekSeconds = seconds(options.fallbackSeekSeconds ?? startSeconds);
  const generatedSeekSeconds = seconds(options.generatedSeekSeconds);

  if (!options.mediaId || !options.projectId) {
    return {
      source: original,
      fallbackSource: "",
      downloadSource: original,
      seekSeconds: fallbackSeekSeconds,
      fallbackSeekSeconds,
      generated: false,
    };
  }

  try {
    const response = await projectsApi.videoPreview(options.scope, options.projectId, options.mediaId, {
      full,
      start_seconds: startSeconds,
      duration_seconds: full ? undefined : Math.max(1, seconds(options.durationSeconds, 8) || 8),
    });
    const source = response.preview_url || response.original_url || original;
    const generated = Boolean(response.generated);
    const originalSource = response.original_url || original;
    return {
      source,
      // If the backend generated an MP4 preview, do not fall back to the original
      // camera AVI/MJPG file. Chromium/WebView often cannot play it, so fallback
      // only replaces a useful error with another guaranteed failure.
      fallbackSource: generated ? "" : originalSource,
      downloadSource: originalSource || source,
      seekSeconds: generated ? generatedSeekSeconds : fallbackSeekSeconds,
      fallbackSeekSeconds,
      generated,
    };
  } catch (error) {
    if (options.scope === "core") {
      throw new Error(`视频预览生成失败：${error instanceof Error ? error.message : "请稍后重试"}`);
    }
    console.warn("resolveVideoPreviewSource:", error);
    return {
      source: original,
      fallbackSource: "",
      downloadSource: original,
      seekSeconds: fallbackSeekSeconds,
      fallbackSeekSeconds,
      generated: false,
    };
  }
}
