import { apiBlob, apiRequest, saveBlob, type AuthScope } from "./http";

export interface SearchFilters {
  camera_id?: string;
  media_id?: number;
  start_time?: string;
  end_time?: string;
  min_similarity?: number;
}

export interface SearchResult {
  id?: number;
  rank?: number;
  target_id?: number;
  path: string;
  thumbnail_url?: string;
  similarity: number;
  source_info: string;
  media_id?: number;
  media_filename?: string;
  media_type?: string;
  camera_id?: string;
  timestamp?: string;
  timestamp_seconds?: number;
  original_media_url?: string;
  original_media_path?: string;
  video_seek_seconds?: number;
  metadata?: Record<string, unknown>;
}

export interface SearchResponse {
  status: string;
  task_id?: number;
  query_object_url?: string | null;
  task?: {
    id: number;
    project_id?: number;
    type?: string;
    content?: string;
    filters?: SearchFilters;
    filter_summary?: string;
    query_object_url?: string | null;
    created_at?: string;
    result_count?: number;
  };
  pagination?: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
    has_previous: boolean;
    has_next: boolean;
  };
  results: SearchResult[];
}

export interface SearchHistoryItem {
  id: number;
  project_id?: number;
  session_id?: string;
  type?: string;
  query_type?: string;
  content?: string;
  query_content?: string;
  filters?: SearchFilters;
  filter_summary?: string;
  query_object_url?: string | null;
  created_at?: string;
  result_count?: number;
  results?: SearchResult[];
}

interface HistoryListResponse {
  status: string;
  history: SearchHistoryItem[];
}

function normalizeHistoryItem(item: SearchHistoryItem): SearchHistoryItem {
  return {
    ...item,
    query_type: item.query_type || item.type,
    query_content: item.query_content || item.content,
  };
}

function normalizeSearchResponse(response: SearchResponse): SearchResponse {
  return {
    ...response,
    task_id: response.task_id || response.task?.id,
    query_object_url: response.query_object_url ?? response.task?.query_object_url ?? null,
  };
}

export const searchApi = {
  text(projectId: number, queryText: string, filters: SearchFilters, page = 1, pageSize = 12, signal?: AbortSignal): Promise<SearchResponse> {
    return apiRequest<SearchResponse>("/api/v1/search/text", {
      method: "POST",
      authScope: "core",
      json: {
        project_id: projectId,
        query_text: queryText,
        top_k: 50,
        page,
        page_size: pageSize,
        filters,
      },
      signal,
    }).then(normalizeSearchResponse);
  },

  image(projectId: number, file: File, filters: SearchFilters, page = 1, pageSize = 12, signal?: AbortSignal): Promise<SearchResponse> {
    const form = new FormData();
    form.append("file", file);
    return apiRequest<SearchResponse>("/api/v1/search/image", {
      method: "POST",
      authScope: "core",
      query: {
        project_id: projectId,
        top_k: 50,
        page,
        page_size: pageSize,
        min_similarity: filters.min_similarity,
        camera_id: filters.camera_id,
        media_id: filters.media_id,
        start_time: filters.start_time,
        end_time: filters.end_time,
      },
      body: form,
      signal,
    }).then(normalizeSearchResponse);
  },

  history(scope: AuthScope = "core", signal?: AbortSignal): Promise<SearchHistoryItem[]> {
    const path = scope === "scenic" ? "/api/v1/scenic/history" : "/api/v1/history";
    return apiRequest<HistoryListResponse>(path, { authScope: scope, signal }).then((response) =>
      (response.history || []).map(normalizeHistoryItem),
    );
  },

  historyPage(scope: AuthScope, historyId: number, page = 1, pageSize = 12, signal?: AbortSignal): Promise<SearchResponse> {
    const path = scope === "scenic" ? `/api/v1/scenic/history/${historyId}` : `/api/v1/history/${historyId}`;
    return apiRequest<SearchResponse>(path, { authScope: scope, query: { page, page_size: pageSize }, signal }).then(
      normalizeSearchResponse,
    );
  },

  result(resultId: number): Promise<SearchResult> {
    return apiRequest<SearchResult>(`/api/v1/search/results/${resultId}`, { authScope: "core" });
  },

  async exportReport(taskId: number, format: "csv" | "json" = "csv"): Promise<void> {
    const blob = await apiBlob(`/api/v1/search/tasks/${taskId}/report`, { authScope: "core", query: { format } });
    await saveBlob(blob, `myreid-search-${taskId}.${format}`);
  },
};
