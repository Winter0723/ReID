import { ref } from "vue";
import { defineStore } from "pinia";

import type { MediaItem } from "../lib/api/media";
import type { CameraStatus, JobItem, Project, ProjectStatus } from "../lib/api/projects";
import type { SearchHistoryItem, SearchResponse, SearchResult } from "../lib/api/search";
import type { UploadTask } from "./types";


export const useCoreStore = defineStore("core", () => {
  const projects = ref<Project[]>([]);
  const projectId = ref(0);
  const projectStatus = ref<ProjectStatus | null>(null);
  const cameraStatus = ref<CameraStatus | null>(null);
  const mediaItems = ref<MediaItem[]>([]);
  const previewUrls = ref<string[]>([]);
  const jobs = ref<JobItem[]>([]);
  const uploadTasks = ref<UploadTask[]>([]);
  const auditRows = ref<Record<string, unknown>[]>([]);
  const searchHistory = ref<SearchHistoryItem[]>([]);
  const searchResults = ref<SearchResult[]>([]);
  const searchResponse = ref<SearchResponse | null>(null);
  const selectedResult = ref<SearchResult | null>(null);

  function resetWorkspace(): void {
    projects.value = [];
    projectId.value = 0;
    projectStatus.value = null;
    cameraStatus.value = null;
    mediaItems.value = [];
    previewUrls.value = [];
    jobs.value = [];
    uploadTasks.value = [];
    auditRows.value = [];
    searchHistory.value = [];
    searchResults.value = [];
    searchResponse.value = null;
    selectedResult.value = null;
  }

  return {
    projects,
    projectId,
    projectStatus,
    cameraStatus,
    mediaItems,
    previewUrls,
    jobs,
    uploadTasks,
    auditRows,
    searchHistory,
    searchResults,
    searchResponse,
    selectedResult,
    resetWorkspace,
  };
});
