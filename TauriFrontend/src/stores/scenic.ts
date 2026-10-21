import { ref } from "vue";
import { defineStore } from "pinia";

import type { MediaItem } from "../lib/api/media";
import type { JobItem, Project, ProjectStatus } from "../lib/api/projects";
import type {
  ScenicCameraPoint,
  ScenicCameraWallItem,
  ScenicDashboard,
  TouristSession, ScenicTouristQuery, ScenicHistoryQuery, ScenicAuditQuery, ScenicPagination, ScenicWorkQueue,
} from "../lib/api/scenic";
import type { SearchHistoryItem } from "../lib/api/search";
import type { UploadTask } from "./types";
import type { DraftRecord } from "../lib/api/scenicEditing";


export function defaultScenicListQuery() { return { page: 1, page_size: 20, q: "", date_from: "", date_to: "" }; }
export function emptyScenicPagination(): ScenicPagination { return { page: 1, page_size: 20, total: 0, total_pages: 0 }; }

export const useScenicStore = defineStore("scenic", () => {
  const projects = ref<Project[]>([]);
  const projectId = ref(0);
  const projectStatus = ref<ProjectStatus | null>(null);
  const mediaItems = ref<MediaItem[]>([]);
  const previewUrls = ref<string[]>([]);
  const jobs = ref<JobItem[]>([]);
  const uploadTasks = ref<UploadTask[]>([]);
  const auditRows = ref<Record<string, unknown>[]>([]);
  const searchHistory = ref<SearchHistoryItem[]>([]);
  const dashboard = ref<ScenicDashboard | null>(null);
  const cameraPoints = ref<ScenicCameraPoint[]>([]);
  const cameraWall = ref<ScenicCameraWallItem[]>([]);
  const tourists = ref<TouristSession[]>([]);
  const touristQuery = ref<ScenicTouristQuery>({ ...defaultScenicListQuery(), status: "", work_queue: "" });
  const touristPagination = ref<ScenicPagination>(emptyScenicPagination());
  const touristQueueCounts = ref<Partial<Record<ScenicWorkQueue, number>>>({});
  const touristLoading = ref(false);
  const touristError = ref("");
  const historyQuery = ref<ScenicHistoryQuery>({ ...defaultScenicListQuery(), query_type: "" });
  const historyPagination = ref<ScenicPagination>(emptyScenicPagination());
  const historyLoading = ref(false);
  const historyError = ref("");
  const auditQuery = ref<ScenicAuditQuery>({ ...defaultScenicListQuery(), action: "" });
  const auditPagination = ref<ScenicPagination>(emptyScenicPagination());
  const auditLoading = ref(false);
  const auditError = ref("");
  function resetLists(): void {
    touristQuery.value = { ...defaultScenicListQuery(), status: "", work_queue: "" };
    touristPagination.value = emptyScenicPagination();
    touristQueueCounts.value = {};
    touristLoading.value = false;
    touristError.value = "";
    historyQuery.value = { ...defaultScenicListQuery(), query_type: "" };
    historyPagination.value = emptyScenicPagination();
    historyLoading.value = false;
    historyError.value = "";
    auditQuery.value = { ...defaultScenicListQuery(), action: "" };
    auditPagination.value = emptyScenicPagination();
    auditLoading.value = false;
    auditError.value = "";
  }
  const selectedTourist = ref<TouristSession | null>(null);
  // Keep unsaved work across visitor selection and page tabs; clear on workspace reset/logout.
  const editingBuffers = ref<Record<string, { record: DraftRecord; saved: string }>>({});
  const editingBackups = ref<Record<string, DraftRecord>>({});

  function resetWorkspace(): void {
    resetLists();
    projects.value = [];
    projectId.value = 0;
    projectStatus.value = null;
    mediaItems.value = [];
    previewUrls.value = [];
    jobs.value = [];
    uploadTasks.value = [];
    auditRows.value = [];
    searchHistory.value = [];
    dashboard.value = null;
    cameraPoints.value = [];
    cameraWall.value = [];
    tourists.value = [];
    selectedTourist.value = null;
    editingBuffers.value = {};
    editingBackups.value = {};
  }

  return {
    projects,
    projectId,
    projectStatus,
    mediaItems,
    previewUrls,
    jobs,
    uploadTasks,
    auditRows,
    searchHistory,
    dashboard,
    cameraPoints,
    cameraWall,
    tourists,
    touristQuery, touristPagination, touristQueueCounts, touristLoading, touristError,
    historyQuery, historyPagination, historyLoading, historyError,
    auditQuery, auditPagination, auditLoading, auditError, resetLists,
    selectedTourist,
    editingBuffers,
    editingBackups,
    resetWorkspace,
  };
});
