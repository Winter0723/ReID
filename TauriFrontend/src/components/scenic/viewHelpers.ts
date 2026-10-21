import type { TouristSegment } from "../../lib/api/scenic";


export function formatSimilarity(value?: number): string {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

export function scenicStatusLabel(status?: string): string {
  const labels: Record<string, string> = {
    registered: "已登记",
    active: "已登记",
    checked_out: "已出园",
    vlog_failed: "成片失败",
    vlog_partial: "部分完成",
    failed: "失败",
    matched: "已匹配",
    no_match: "无匹配",
    rendering: "成片中",
    vlog_processing: "成片中",
    vlog_ready: "可交付",
    paid: "已付款",
  };
  return labels[status || ""] || status || "已登记";
}

export function scenicStatusClass(status?: string): string {
  if (["paid", "vlog_ready", "matched"].includes(status || "")) return "status-ok";
  if (["failed", "vlog_failed", "no_match"].includes(status || "")) return "status-danger";
  if (["rendering", "vlog_processing", "vlog_partial"].includes(status || "")) return "status-warn";
  return "status-info";
}

export function segmentDuration(segment: TouristSegment): string {
  const start = Number(segment.start_seconds || 0);
  const end = Number(segment.end_seconds || start);
  return `${start.toFixed(1)}s - ${Math.max(start, end).toFixed(1)}s`;
}

export function segmentDurationSeconds(segment: TouristSegment): number {
  const start = Number(segment.start_seconds || 0);
  const end = Number(segment.end_seconds || start);
  return Math.max(1, Number(segment.duration_seconds || 0) || Math.max(start, end) - start || 8);
}

export function segmentVideoAvailable(segment: TouristSegment): boolean {
  return Boolean(segment.media_id || segment.original_media_url);
}
