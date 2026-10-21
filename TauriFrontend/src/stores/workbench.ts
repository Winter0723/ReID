import { computed } from "vue";
import { defineStore, storeToRefs } from "pinia";
import { authApi } from "../lib/api/auth";
import { mediaApi, type MediaItem, type UploadItem, type UploadResponse } from "../lib/api/media";
import { projectsApi, type JobItem } from "../lib/api/projects";
import { scenicApi, type ScenicOrder, type ScenicSnapshot, type TouristSession, type ScenicTouristQuery, type ScenicPagination, type ScenicListQuery } from "../lib/api/scenic";
import { searchApi, type SearchFilters, type SearchHistoryItem } from "../lib/api/search";
import { normalizeAssetUrl, readToken, subscribeAuthSession, type AuthScope } from "../lib/api/http";
import { systemApi, type CameraDevice, type MetricsPayload } from "../lib/api/system";
import { createLatestRequestGate } from "../lib/latestRequest";
import { useAuthStore } from "./auth";
import { useCoreStore } from "./core";
import { useScenicStore } from "./scenic";
import { useSystemStore } from "./system";
import type { UploadTask, WorkbenchMode, WorkbenchPreferences } from "./types";

export type { LoginForm, UploadTask, WorkbenchMode, WorkbenchPreferences } from "./types";

function itemText(item: unknown, key: string): string {
  if (typeof item !== "object" || item === null || !(key in item)) return "";
  const value = (item as Record<string, unknown>)[key];
  return value === null || value === undefined ? "" : String(value);
}

/**
 * Backward-compatible orchestration facade.
 *
 * Domain state is owned by the auth/core/scenic/system stores below. Keeping
 * this action facade lets the existing views retain their public API while
 * request cancellation and cross-domain workflows are migrated incrementally.
 */
export const useWorkbenchStore = defineStore("workbench", () => {
  const authStore = useAuthStore();
  const coreStore = useCoreStore();
  const scenicStore = useScenicStore();
  const systemStore = useSystemStore();
  const {
    coreAccessToken,
    scenicAccessToken,
    coreUser,
    scenicUser,
    coreLogin,
    scenicLogin,
  } = storeToRefs(authStore);
  const {
    projects: coreProjects,
    projectId: coreProjectId,
    projectStatus: coreProjectStatus,
    cameraStatus: coreCameraStatus,
    mediaItems,
    previewUrls: corePreviewUrls,
    jobs: coreJobs,
    uploadTasks: coreUploadTasks,
    auditRows: coreAuditRows,
    searchHistory: coreSearchHistory,
    searchResults,
    searchResponse,
    selectedResult,
  } = storeToRefs(coreStore);
  const {
    projects: scenicProjects,
    projectId: scenicProjectId,
    projectStatus: scenicProjectStatus,
    mediaItems: scenicMediaItems,
    previewUrls: scenicPreviewUrls,
    jobs: scenicJobs,
    uploadTasks: scenicUploadTasks,
    auditRows: scenicAuditRows,
    searchHistory: scenicSearchHistory,
    dashboard: scenicDashboard,
    cameraPoints: scenicCameraPoints,
    cameraWall: scenicCameraWall,
    tourists,
    selectedTourist,
    touristQuery, touristPagination, touristQueueCounts, touristLoading, touristError,
    historyQuery: scenicHistoryQuery, historyPagination: scenicHistoryPagination, historyLoading: scenicHistoryLoading, historyError: scenicHistoryError,
    auditQuery: scenicAuditQuery, auditPagination: scenicAuditPagination, auditLoading: scenicAuditLoading, auditError: scenicAuditError,
  } = storeToRefs(scenicStore);
  const {
    readyz,
    metrics,
    observability,
    cameraDevices,
    scenicCameraDevices,
    preferences,
    mode,
    activeTaskCount,
    message,
    error,
  } = storeToRefs(systemStore);

  const busy = computed(() => activeTaskCount.value > 0);
  const latestRequest = createLatestRequestGate();
  let foregroundTaskVersion = 0;
  let livePollInFlight = false;
  let coreFramePoll: { projectId: number } | null = null;
  let scenicCameraInitialization: { projectId: number } | null = null;
  let scenicFramePoll: { projectId: number } | null = null;
  let lastCoreActivityPollAt = 0;
  let lastScenicActivityPollAt = 0;
  let scenicPreview: { projectId: number; pointId: number; refreshedAt: number } | null = null;
  const heavyLivePollIntervalMs = 6000;

  function beginForegroundTask(label: string): number {
    const version = ++foregroundTaskVersion;
    activeTaskCount.value += 1;
    error.value = "";
    message.value = label;
    return version;
  }

  function finishForegroundTask(version: number, nextMessage: string, nextError = ""): void {
    if (version !== foregroundTaskVersion) return;
    message.value = nextMessage;
    error.value = nextError;
  }

  function endForegroundTask(): void {
    activeTaskCount.value = Math.max(0, activeTaskCount.value - 1);
  }

  const activeToken = computed(() => (mode.value === "core" ? coreAccessToken.value : scenicAccessToken.value));
  const isAuthenticated = computed(() => Boolean(activeToken.value));
  const activeUser = computed(() => (mode.value === "core" ? coreUser.value : scenicUser.value));
  const activeProjects = computed(() => (mode.value === "core" ? coreProjects.value : scenicProjects.value));
  const activeProjectId = computed(() => (mode.value === "core" ? coreProjectId.value : scenicProjectId.value));
  const activeProject = computed(() => activeProjects.value.find((project) => project.id === activeProjectId.value) || null);
  const activeStatus = computed(() => (mode.value === "core" ? coreProjectStatus.value : scenicProjectStatus.value));
  const activeIsCameraProject = computed(() => mode.value === "core" && activeProject.value?.project_type === "camera");
  const auditRows = computed(() => (mode.value === "core" ? coreAuditRows.value : scenicAuditRows.value));
  const searchHistory = computed(() => (mode.value === "core" ? coreSearchHistory.value : scenicSearchHistory.value));
  const previewUrls = computed(() => (mode.value === "core" ? corePreviewUrls.value : scenicPreviewUrls.value));
  const jobs = computed(() => (mode.value === "core" ? coreJobs.value : scenicJobs.value));
  const uploadTasks = computed(() => {
    const projectId = activeProjectId.value;
    const scopedTasks = mode.value === "core" ? coreUploadTasks.value : scenicUploadTasks.value;
    return scopedTasks.filter((task) => task.projectId === projectId);
  });
  const activeJobs = computed(() => {
    const uploadJobIds = new Set(uploadTasks.value.map((task) => task.jobId).filter(Boolean));
    return jobs.value.filter((job) => (job.status === "queued" || job.status === "running") && !uploadJobIds.has(job.id));
  });
  const systemReady = computed(() => ["ready", "degraded", "ok"].includes(readyz.value?.status || ""));

  subscribeAuthSession(({ scope, authenticated, reason }) => {
    const token = authenticated ? readToken(scope) : "";
    if (!authenticated) {
      latestRequest.invalidate(`profile:${scope}`);
      latestRequest.invalidate(`projects:${scope}`);
      latestRequest.invalidate(`workspace:${scope}`);
      latestRequest.invalidate(`search-history:${scope}`);
      latestRequest.invalidate(`audit:${scope}`);
      latestRequest.invalidate("live-poll");
      if (scope === "core") {
        latestRequest.invalidate("core-camera-status");
        coreFramePoll = null;
      }
      if (scope === "scenic") {
        latestRequest.invalidate("scenic-tourists");
        latestRequest.invalidate("scenic-workspace");
        resetScenicCameraInitialization();
        releaseScenicCameraPreview();
        latestRequest.invalidate("scenic-camera-points");
        latestRequest.invalidate("scenic-camera-wall");
      }
    }
    if (scope === "core") {
      authStore.updateSession("core", token, authenticated ? coreUser.value : null);
      if (!authenticated) {
        coreStore.resetWorkspace();
      }
    } else {
      authStore.updateSession("scenic", token, authenticated ? scenicUser.value : null);
      if (!authenticated) {
        scenicStore.resetWorkspace();
      }
    }
    if (reason === "expired" && mode.value === scope) {
      error.value = "登录状态已过期，请重新登录";
      message.value = "需要重新登录";
    }
  });

  function updatePreferences(patch: Partial<WorkbenchPreferences>): void {
    systemStore.updatePreferences(patch);
  }

  function isCoreCameraProject(projectId = coreProjectId.value): boolean {
    return coreProjects.value.find((project) => project.id === projectId)?.project_type === "camera";
  }

  function jobsFor(scope: AuthScope) {
    return scope === "core" ? coreJobs : scenicJobs;
  }

  function uploadTasksFor(scope: AuthScope) {
    return scope === "core" ? coreUploadTasks : scenicUploadTasks;
  }

  function previewUrlsFor(scope: AuthScope) {
    return scope === "core" ? corePreviewUrls : scenicPreviewUrls;
  }

  function mergeSelectedTouristSummary(rows: TouristSession[]): void {
    if (!selectedTourist.value) return;
    const summary = rows.find((tourist) => tourist.id === selectedTourist.value?.id);
    if (!summary) return;
    selectedTourist.value = {
      ...selectedTourist.value,
      ...summary,
      segments: selectedTourist.value.segments,
      vlog_jobs: selectedTourist.value.vlog_jobs,
      orders: selectedTourist.value.orders,
    };
  }

  function mergeSelectedTouristOrder(order: ScenicOrder): void {
    if (!selectedTourist.value) return;
    const orders = selectedTourist.value.orders || [];
    const existingIndex = orders.findIndex((item) => item.id === order.id);
    const nextOrders = existingIndex >= 0
      ? orders.map((item, index) => (index === existingIndex ? order : item))
      : [order, ...orders];
    selectedTourist.value = { ...selectedTourist.value, orders: nextOrders };
  }

  function setMode(nextMode: WorkbenchMode): void {
    if (mode.value === nextMode) return;
    releaseScenicCameraPreview();
    resetScenicCameraInitialization();
    mode.value = nextMode;
    latestRequest.invalidate("core-camera-status");
    coreFramePoll = null;
    latestRequest.invalidate("live-poll");
    error.value = "";
    if (readToken(nextMode)) void loadSearchHistory(nextMode);
    if (nextMode === "scenic" && scenicAccessToken.value && scenicProjectId.value) {
      void refreshWorkspace("scenic");
    } else if (nextMode === "core" && coreAccessToken.value && coreProjectId.value) {
      void refreshWorkspace("core");
    }
  }

  function createTaskId(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function uploadItemsFromResponse(response: UploadResponse, files: File[]): UploadItem[] {
    if (Array.isArray(response.items)) return response.items;
    return [{
      media_id: response.media_id,
      existing_media_id: response.existing_media_id,
      job_id: response.job_id,
      filename: files[0]?.name,
      status: response.status,
    }];
  }

  function syncUploadTasksFromJobs(nextJobs: JobItem[] = jobs.value, scope: AuthScope = mode.value): void {
    const scopedUploadTasks = uploadTasksFor(scope);
    const jobsById = new Map(nextJobs.map((job) => [job.id, job]));
    scopedUploadTasks.value = scopedUploadTasks.value.map((task) => {
      if (!task.jobId) return task;
      const job = jobsById.get(task.jobId);
      if (!job) return task;
      const failed = job.status === "failed";
      const succeeded = job.status === "succeeded";
      return {
        ...task,
        status: failed ? "failed" : succeeded ? "succeeded" : job.status === "running" ? "running" : "queued",
        progress: Math.max(task.progress, Number(job.progress ?? 0)),
        message: job.message || task.message,
        error: job.error || task.error,
      };
    });
  }

  async function refreshCoreProjectActivity(projectId = coreProjectId.value): Promise<void> {
    if (!coreAccessToken.value || !projectId) return;
    const ticket = latestRequest.begin("core-project-activity");
    const [status, recentJobs, media] = await Promise.all([
      projectsApi.status("core", projectId, ticket.signal).catch(() => coreProjectStatus.value),
      projectsApi.jobs("core", projectId, ticket.signal).catch(() => coreJobs.value),
      mediaApi.list(projectId, "core", ticket.signal).catch(() => mediaItems.value),
    ]);
    if (!latestRequest.isCurrent(ticket) || coreProjectId.value !== projectId) return;
    if (status) coreProjectStatus.value = status;
    coreJobs.value = recentJobs;
    mediaItems.value = media;
    syncUploadTasksFromJobs(recentJobs, "core");
  }

  async function setProject(scope: AuthScope, projectId: number): Promise<void> {
    const ticket = latestRequest.begin(`project-selection:${scope}`);
    latestRequest.invalidate(`workspace:${scope}`);
    latestRequest.invalidate("live-poll");
    if (scope === "core") {
      latestRequest.invalidate("core-camera-status");
      coreFramePoll = null;
      coreCameraStatus.value = null;
      coreProjectId.value = projectId;
      lastCoreActivityPollAt = 0;
    } else {
      latestRequest.invalidate("scenic-tourists");
      latestRequest.invalidate("scenic-camera-points");
      latestRequest.invalidate("scenic-camera-wall");
      if (scenicProjectId.value !== projectId) {
        resetScenicCameraInitialization();
        releaseScenicCameraPreview();
        scenicCameraPoints.value = [];
        scenicCameraWall.value = [];
      }
      latestRequest.invalidate("search-history:scenic");
      latestRequest.invalidate("audit:scenic");
      scenicStore.resetLists();
      tourists.value = [];
      scenicSearchHistory.value = [];
      scenicAuditRows.value = [];
      scenicProjectId.value = projectId;
      lastScenicActivityPollAt = 0;
      selectedTourist.value = null;
    }
    if (!latestRequest.isCurrent(ticket)) return;
    await refreshWorkspace(scope);
    if (!latestRequest.isCurrent(ticket)) return;
    await loadSearchHistory(scope);
    if (!latestRequest.isCurrent(ticket)) return;
  }

  async function runTask<T>(label: string, task: () => Promise<T>): Promise<T | null> {
    const taskVersion = beginForegroundTask(label);
    try {
      const result = await task();
      finishForegroundTask(taskVersion, message.value === label ? `${label}：操作成功` : message.value);
      return result;
    } catch (taskError) {
      finishForegroundTask(
        taskVersion,
        "操作失败",
        taskError instanceof Error ? taskError.message : String(taskError),
      );
      return null;
    } finally {
      endForegroundTask();
    }
  }

  async function refreshSystem(forceCameraScan = true): Promise<void> {
    const ticket = latestRequest.begin("system-refresh");
    await runTask("刷新系统状态", async () => {
      const nextReadyz = await systemApi.readyz(ticket.signal).catch(() => ({ status: "offline" }));
      let forcedCameraScanCompleted = false;
      let nextMetrics: MetricsPayload | null = null;
      let nextObservability: Record<string, unknown> | null = null;
      let nextCoreCameras = cameraDevices.value;
      let nextScenicCameras = scenicCameraDevices.value;
      if (coreAccessToken.value) {
        const canReadGlobalObservability = coreUser.value?.role === "admin";
        [nextMetrics, nextObservability, nextCoreCameras] = await Promise.all([
          systemApi.metrics(ticket.signal).catch(() => null),
          canReadGlobalObservability ? systemApi.observability(ticket.signal).catch(() => null) : Promise.resolve(null),
          systemApi.cameras("core", forceCameraScan, ticket.signal).then((response) => response.devices).catch(() => [] as CameraDevice[]),
        ]);
        forcedCameraScanCompleted = forceCameraScan;
      }
      if (scenicAccessToken.value) {
        nextScenicCameras = (
          await systemApi.scenicCameras(forceCameraScan && !forcedCameraScanCompleted, ticket.signal).catch(() => ({ devices: [] as CameraDevice[] }))
        ).devices;
      }
      if (!latestRequest.isCurrent(ticket)) return;
      readyz.value = nextReadyz;
      if (coreAccessToken.value) {
        metrics.value = nextMetrics;
        observability.value = coreUser.value?.role === "admin" ? nextObservability : null;
        cameraDevices.value = nextCoreCameras;
      }
      if (scenicAccessToken.value) scenicCameraDevices.value = nextScenicCameras;
    });
  }

  async function loadAuditLogs(scope: AuthScope = mode.value): Promise<void> {
    const ticket = latestRequest.begin(`audit:${scope}`);
    try {
      if (scope === "core") {
        if (coreUser.value?.role === "admin") {
          const data = await systemApi.observability(ticket.signal);
          if (!latestRequest.isCurrent(ticket)) return;
          coreAuditRows.value = data && Array.isArray(data.logs)
            ? data.logs as Record<string, unknown>[]
            : [];
        } else if (coreProjectId.value) {
          const projectId = coreProjectId.value;
          const rows = (await projectsApi.audit("core", projectId, ticket.signal)) as Record<string, unknown>[];
          if (!latestRequest.isCurrent(ticket) || coreProjectId.value !== projectId) return;
          coreAuditRows.value = rows;
        } else {
          coreAuditRows.value = [];
        }
        return;
      }
      if (scenicProjectId.value) {
        const projectId = scenicProjectId.value;
        scenicAuditLoading.value = true;
        scenicAuditError.value = "";
        const query = { ...scenicAuditQuery.value };
        const response = await scenicApi.audit(projectId, query, ticket.signal);
        if (!latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
        scenicAuditRows.value = response.audit || [];
        scenicAuditPagination.value = listPagination(response.pagination, scenicAuditRows.value.length, query);
      }
    } catch (e: any) {
      if (scope === "scenic" && latestRequest.isCurrent(ticket)) scenicAuditError.value = e.message || "审计加载失败，请重试";
      else if (scope === "core") console.warn("loadAuditLogs:", e.message);
    } finally {
      if (scope === "scenic" && latestRequest.isCurrent(ticket)) scenicAuditLoading.value = false;
    }
  }

  async function bootstrap(): Promise<void> {
    const taskVersion = beginForegroundTask("加载系统配置");
    try {
      await refreshSystem(false);
      if (coreAccessToken.value) {
        try {
          await loadProfile("core");
          await loadProjects("core");
          await loadAuditLogs("core");
        } catch (bootstrapError) {
          if (readToken("core")) {
            error.value = bootstrapError instanceof Error ? bootstrapError.message : String(bootstrapError);
          }
        }
      }
      if (scenicAccessToken.value) {
        try {
          await loadProfile("scenic");
          await loadProjects("scenic");
          await loadAuditLogs("scenic");
        } catch (bootstrapError) {
          if (readToken("scenic")) {
            error.value = bootstrapError instanceof Error ? bootstrapError.message : String(bootstrapError);
          }
        }
      }
    } finally {
      finishForegroundTask(taskVersion, error.value ? "加载完成，部分服务不可用" : "系统已就绪", error.value);
      endForegroundTask();
    }
  }

  async function login(scope: AuthScope): Promise<void> {
    const form = scope === "core" ? coreLogin.value : scenicLogin.value;
    await runTask("登录中", async () => {
      const token = await authApi.login(scope, form.account, form.password);
      if (scope === "core") coreAccessToken.value = token.access_token;
      if (scope === "scenic") scenicAccessToken.value = token.access_token;
      await loadProfile(scope);
      await loadProjects(scope);
    });
  }

  async function register(scope: AuthScope): Promise<void> {
    const form = scope === "core" ? coreLogin.value : scenicLogin.value;
    await runTask("注册账号", async () => {
      const token = await authApi.register(scope, form.username, form.email, form.registerPassword, form.code);
      if (scope === "core") coreAccessToken.value = token.access_token;
      if (scope === "scenic") scenicAccessToken.value = token.access_token;
      await loadProfile(scope);
      await loadProjects(scope);
    });
  }

  async function requestEmailCode(scope: AuthScope, purpose: "register" | "login"): Promise<void> {
    const form = scope === "core" ? coreLogin.value : scenicLogin.value;
    await runTask("请求验证码", async () => {
      const response = await authApi.requestEmailCode(scope, form.email, purpose, form.username);
      form.debugCode = response.debug_code || "";
      message.value = response.debug_code ? `验证码 ${response.debug_code}` : response.message;
    });
  }

  async function logout(scope: AuthScope): Promise<void> {
    await runTask("退出登录", async () => {
      await authApi.logout(scope);
      if (scope === "core") {
        coreAccessToken.value = "";
        coreUser.value = null;
      } else {
        scenicAccessToken.value = "";
        scenicUser.value = null;
      }
    });
  }

  async function loadProfile(scope: AuthScope): Promise<void> {
    const ticket = latestRequest.begin(`profile:${scope}`);
    const accessToken = readToken(scope);
    const profile = await authApi.me(scope, ticket.signal);
    if (!latestRequest.isCurrent(ticket) || readToken(scope) !== accessToken) return;
    if (scope === "core") coreUser.value = profile;
    if (scope === "scenic") scenicUser.value = profile;
  }

  async function loadProjects(scope: AuthScope): Promise<void> {
    const ticket = latestRequest.begin(`projects:${scope}`);
    const rows = await projectsApi.list(scope, ticket.signal);
    if (!latestRequest.isCurrent(ticket)) return;
    if (scope === "core") {
      coreProjects.value = rows;
      coreProjectId.value = rows.some((project) => project.id === coreProjectId.value)
        ? coreProjectId.value
        : rows[0]?.id || 0;
      if (!isCoreCameraProject(coreProjectId.value)) coreCameraStatus.value = null;
    } else {
      scenicProjects.value = rows;
      scenicProjectId.value = rows.some((project) => project.id === scenicProjectId.value)
        ? scenicProjectId.value
        : rows[0]?.id || 0;
    }
    if ((scope === "core" && coreProjectId.value) || (scope === "scenic" && scenicProjectId.value)) {
      await refreshWorkspace(scope);
      if (!latestRequest.isCurrent(ticket)) return;
      await loadSearchHistory(scope);
      if (!latestRequest.isCurrent(ticket)) return;
    }
  }

  async function createProject(scope: AuthScope, name: string, description: string, projectType = "media", cameraSource = "0"): Promise<void> {
    await runTask("创建项目", async () => {
      const project = await projectsApi.create(scope, { name, description, project_type: projectType, camera_source: cameraSource });
      if (scope === "core") coreProjectId.value = project.id;
      else scenicProjectId.value = project.id;
      await loadProjects(scope);
    });
  }

  async function refreshWorkspace(scope: AuthScope = mode.value): Promise<void> {
    const projectId = scope === "core" ? coreProjectId.value : scenicProjectId.value;
    if (!projectId) return;
    const ticket = latestRequest.begin(`workspace:${scope}`);
    await runTask("刷新工作区", async () => {
      const isCameraProject = scope === "core" && isCoreCameraProject(projectId);
      const [status, preview, recentJobs, , coreMedia] = await Promise.all([
        projectsApi.status(scope, projectId, ticket.signal),
        projectsApi.preview(scope, projectId, ticket.signal).catch(() => ({ previews: [] as string[] })),
        projectsApi.jobs(scope, projectId, ticket.signal).catch(() => [] as JobItem[]),
        scope === "scenic" ? loadAuditLogs("scenic") : Promise.resolve(),
        scope === "core" ? mediaApi.list(projectId, "core", ticket.signal).catch(() => [] as MediaItem[]) : Promise.resolve([] as MediaItem[]),
        isCameraProject ? refreshCoreCameraStatus() : Promise.resolve(),
        // Camera state must arrive even while project statistics or history are slow.
        scope === "scenic" ? refreshScenicWorkspace(projectId) : Promise.resolve(),
      ]);
      if (
        !latestRequest.isCurrent(ticket)
        || (scope === "core" ? coreProjectId.value : scenicProjectId.value) !== projectId
      ) return;
      if (scope === "core") {
        coreProjectStatus.value = status;
        mediaItems.value = coreMedia;
        if (!isCameraProject) coreCameraStatus.value = null;
      } else {
        scenicProjectStatus.value = status;
      }
      previewUrlsFor(scope).value = (preview.previews || []).map(normalizeAssetUrl);
      jobsFor(scope).value = recentJobs;
      syncUploadTasksFromJobs(recentJobs, scope);
    });
  }

  async function uploadMedia(files: File[]): Promise<void> {
    const projectId = coreProjectId.value;
    if (!projectId || files.length === 0) return;
    const pendingTasks: UploadTask[] = files.map((file) => ({
      id: createTaskId(),
      projectId,
      filename: file.name,
      status: "uploading",
      progress: 0.02,
      message: "正在上传媒体",
    }));
    coreUploadTasks.value = [...pendingTasks, ...coreUploadTasks.value].slice(0, 30);
    const taskVersion = beginForegroundTask(`正在上传 ${files.length} 个媒体`);
    try {
      const response = await mediaApi.upload(projectId, files);
      const items = uploadItemsFromResponse(response, files);
      pendingTasks.forEach((task, index) => {
        const item = items[index] || items[0];
        task.mediaId = item?.media_id || item?.existing_media_id;
        task.jobId = item?.job_id || undefined;
        task.status = item?.status === "duplicate" ? "duplicate" : item?.job_id ? "queued" : "succeeded";
        task.progress = item?.status === "duplicate" ? 1 : item?.job_id ? 0.08 : 1;
        task.message = item?.status === "duplicate" ? "已存在相同媒体，跳过重复入库" : item?.job_id ? "已提交特征提取任务" : "上传完成";
      });
      coreUploadTasks.value = [...coreUploadTasks.value];
      finishForegroundTask(taskVersion, "上传完成，正在后台提取特征");
      await refreshCoreProjectActivity(projectId);
    } catch (taskError) {
      const detail = taskError instanceof Error ? taskError.message : String(taskError);
      pendingTasks.forEach((task) => {
        task.status = "failed";
        task.progress = 1;
        task.message = "上传失败";
        task.error = detail;
      });
      coreUploadTasks.value = [...coreUploadTasks.value];
      finishForegroundTask(taskVersion, "上传失败", detail);
    } finally {
      endForegroundTask();
    }
  }

  async function refreshCoreCameraStatus(light = false): Promise<void> {
    const projectId = coreProjectId.value;
    const ticket = latestRequest.begin("core-camera-status");
    if (!coreAccessToken.value || !projectId || !isCoreCameraProject(projectId)) {
      coreCameraStatus.value = null;
      return;
    }
    try {
      const previous = coreCameraStatus.value;
      const nextStatus = await projectsApi.cameraStatus(projectId, light, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || coreProjectId.value !== projectId) return;
      if (!nextStatus.running && previous?.error && !nextStatus.error) {
        nextStatus.error = previous.error;
        nextStatus.message = nextStatus.message || previous.message;
        nextStatus.camera_source = nextStatus.camera_source || previous.camera_source;
      }
      coreCameraStatus.value = nextStatus;
    } catch (e: any) {
      if (latestRequest.isCurrent(ticket)) console.warn("refreshCoreCameraStatus:", e.message);
    }
  }

  async function startCoreCamera(cameraSource = "0"): Promise<void> {
    const projectId = coreProjectId.value;
    await runTask("启用实时入库", async () => {
      if (!projectId || !isCoreCameraProject(projectId)) return;
      const source = cameraSource || coreCameraStatus.value?.camera_source || activeProject.value?.camera_source || "0";
      try {
        const nextStatus = await projectsApi.startCamera(projectId, source);
        if (coreProjectId.value !== projectId) return;
        coreCameraStatus.value = nextStatus;
      } catch (taskError) {
        const detail = taskError instanceof Error ? taskError.message : String(taskError);
        coreCameraStatus.value = {
          ...(coreCameraStatus.value || {}),
          running: false,
          camera_source: source,
          message: "相机启动失败",
          error: detail,
        };
        throw taskError;
      }
      if (coreProjectId.value === projectId) await refreshWorkspace("core");
    });
  }

  async function stopCoreCamera(): Promise<void> {
    const projectId = coreProjectId.value;
    await runTask("暂停实时入库", async () => {
      if (!projectId || !isCoreCameraProject(projectId)) return;
      const nextStatus = await projectsApi.stopCamera(projectId);
      if (coreProjectId.value !== projectId) return;
      coreCameraStatus.value = nextStatus;
      await refreshWorkspace("core");
    });
  }

  async function pollCoreCameraFrames(projectId: number): Promise<void> {
    if (coreFramePoll?.projectId === projectId) return;
    const poll = { projectId };
    coreFramePoll = poll;
    try {
      await refreshCoreCameraStatus(true);
    } finally {
      if (coreFramePoll === poll) coreFramePoll = null;
    }
  }

  async function pollScenicCameraFrames(projectId: number): Promise<void> {
    // Renew before the frame-poll guard: a slow frame or activity request must
    // not let the selected camera's preview lease expire.
    if (scenicPreview?.projectId === projectId && Date.now() - scenicPreview.refreshedAt >= 5000) {
      await prepareScenicCameraPreview(scenicPreview.pointId, true);
    }
    if (mode.value !== "scenic" || scenicProjectId.value !== projectId || !scenicAccessToken.value) return;
    if (scenicFramePoll?.projectId === projectId) return;
    const poll = { projectId };
    scenicFramePoll = poll;
    try {
      await refreshScenicCameraWall(projectId, true);
    } finally {
      if (scenicFramePoll === poll) scenicFramePoll = null;
    }
  }

  async function pollLiveFeeds(): Promise<void> {
    const pollMode = mode.value;
    const coreProjectAtStart = coreProjectId.value;
    const scenicProjectAtStart = scenicProjectId.value;
    // Live camera frames are independent of optional activity/statistics refresh.
    if (pollMode === "core" && coreAccessToken.value && isCoreCameraProject(coreProjectAtStart)) {
      await pollCoreCameraFrames(coreProjectAtStart);
      if (mode.value !== pollMode || coreProjectId.value !== coreProjectAtStart) return;
    }
    if (pollMode === "scenic" && scenicAccessToken.value && scenicProjectAtStart
      && (preferences.value.autoRefresh || scenicPreview)) {
      await pollScenicCameraFrames(scenicProjectAtStart);
      if (mode.value !== pollMode || scenicProjectId.value !== scenicProjectAtStart) return;
    }
    if (!preferences.value.autoRefresh || livePollInFlight) return;
    livePollInFlight = true;
    const ticket = latestRequest.begin("live-poll");
    try {
      const now = Date.now();
      if (pollMode === "core") {
        if (!latestRequest.isCurrent(ticket) || mode.value !== pollMode || coreProjectId.value !== coreProjectAtStart) return;
        if (now - lastCoreActivityPollAt >= heavyLivePollIntervalMs) {
          lastCoreActivityPollAt = now;
          await refreshCoreProjectActivity(coreProjectAtStart);
        }
        return;
      }
      if (!scenicAccessToken.value || !scenicProjectAtStart) return;
      if (!latestRequest.isCurrent(ticket) || mode.value !== pollMode || scenicProjectId.value !== scenicProjectAtStart) return;
      if (preferences.value.autoRefresh && now - lastScenicActivityPollAt >= heavyLivePollIntervalMs) {
        lastScenicActivityPollAt = now;
        const selectedTouristId = selectedTourist.value?.id || 0;
        const [status, recentJobs, , selectedDetail] = await Promise.all([
          projectsApi.status("scenic", scenicProjectAtStart, ticket.signal).catch(() => scenicProjectStatus.value),
          projectsApi.jobs("scenic", scenicProjectAtStart, ticket.signal).catch(() => scenicJobs.value),
          loadScenicTourists(undefined, true),
          selectedTouristId ? scenicApi.tourist(selectedTouristId, ticket.signal).catch(() => null) : Promise.resolve(null),
        ]);
        if (!latestRequest.isCurrent(ticket) || mode.value !== pollMode || scenicProjectId.value !== scenicProjectAtStart) return;
        if (status) scenicProjectStatus.value = status;
        scenicJobs.value = recentJobs;
        syncUploadTasksFromJobs(recentJobs, "scenic");
        if (selectedDetail && selectedTourist.value?.id === selectedTouristId) selectedTourist.value = selectedDetail;
        else mergeSelectedTouristSummary(tourists.value);
      }
    } catch (e: any) {
      console.warn("pollLiveFeeds:", e.message);
    } finally {
      livePollInFlight = false;
    }
  }

  async function rebuildIndex(): Promise<void> {
    await runTask("重建索引", async () => {
      if (!coreProjectId.value) return;
      await projectsApi.rebuildIndex(coreProjectId.value);
      await refreshWorkspace("core");
    });
  }

  async function runTextSearch(queryText: string, filters: SearchFilters): Promise<void> {
    const projectId = coreProjectId.value;
    const ticket = latestRequest.begin("search-results");
    await runTask("执行文本检索", async () => {
      if (!projectId || !queryText.trim()) return;
      const response = await searchApi.text(projectId, queryText.trim(), filters, 1, 12, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || coreProjectId.value !== projectId) return;
      searchResponse.value = response;
      searchResults.value = response.results;
      selectedResult.value = response.results[0] || null;
      void loadSearchHistory("core");
    });
  }

  async function runImageSearch(file: File | null, filters: SearchFilters): Promise<void> {
    const projectId = coreProjectId.value;
    const ticket = latestRequest.begin("search-results");
    await runTask("执行以图搜图", async () => {
      if (!projectId || !file) return;
      const response = await searchApi.image(projectId, file, filters, 1, 12, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || coreProjectId.value !== projectId) return;
      searchResponse.value = response;
      searchResults.value = response.results;
      selectedResult.value = response.results[0] || null;
      void loadSearchHistory("core");
    });
  }

  async function loadSearchHistory(scope: AuthScope = mode.value): Promise<void> {
    if (!readToken(scope)) {
      if (scope === "core") coreSearchHistory.value = [];
      else scenicSearchHistory.value = [];
      return;
    }
    const ticket = latestRequest.begin(`search-history:${scope}`);
    if (scope === "core") {
      const rows = await searchApi.history(scope, ticket.signal).catch(() => [] as SearchHistoryItem[]);
      if (latestRequest.isCurrent(ticket)) coreSearchHistory.value = rows;
      return;
    }
    const projectId = scenicProjectId.value;
    if (!projectId) return;
    scenicHistoryLoading.value = true;
    scenicHistoryError.value = "";
    const query = { ...scenicHistoryQuery.value };
    try {
      const response = await scenicApi.history(projectId, query, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
      scenicSearchHistory.value = response.history || [];
      scenicHistoryPagination.value = listPagination(response.pagination, scenicSearchHistory.value.length, query);
    } catch (cause) {
      if (latestRequest.isCurrent(ticket)) scenicHistoryError.value = cause instanceof Error ? cause.message : "历史加载失败，请重试";
    } finally {
      if (latestRequest.isCurrent(ticket)) scenicHistoryLoading.value = false;
    }
  }

  function listPagination(pagination: ScenicPagination | undefined, count: number, query: ScenicListQuery): ScenicPagination {
    return pagination || { page: query.page, page_size: query.page_size, total: count, total_pages: count ? 1 : 0 };
  }

  async function loadScenicTourists(patch?: Partial<ScenicTouristQuery>, background = false): Promise<void> {
    if (background && touristLoading.value) return;
    if (patch) touristQuery.value = { ...touristQuery.value, ...patch };
    const projectId = scenicProjectId.value;
    if (!projectId || !scenicAccessToken.value) return;
    const ticket = latestRequest.begin("scenic-tourists");
    const query = { ...touristQuery.value };
    touristLoading.value = true;
    touristError.value = "";
    try {
      const response = await scenicApi.tourists(projectId, ticket.signal, query);
      if (!latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
      const pagination = listPagination(response.pagination, response.tourists.length, query);
      // A queue can shrink while another operator works; return to its last available page.
      if (query.page > Math.max(1, pagination.total_pages)) {
        touristLoading.value = false;
        await loadScenicTourists({ page: Math.max(1, pagination.total_pages) });
        return;
      }
      tourists.value = response.tourists;
      touristPagination.value = pagination;
      touristQueueCounts.value = response.work_queue_counts || {};
      mergeSelectedTouristSummary(tourists.value);
    } catch (cause) {
      if (latestRequest.isCurrent(ticket)) touristError.value = cause instanceof Error ? cause.message : "游客加载失败，请重试";
    } finally {
      if (latestRequest.isCurrent(ticket)) touristLoading.value = false;
    }
  }

  async function loadHistoryPage(historyId: number, page = 1, scope: AuthScope = mode.value): Promise<void> {
    const ticket = latestRequest.begin("search-results");
    await runTask("加载搜索历史", async () => {
      const response = await searchApi.historyPage(scope, historyId, page, 12, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || mode.value !== scope) return;
      searchResponse.value = response;
      searchResults.value = response.results || [];
      selectedResult.value = searchResults.value[0] || null;
    });
  }

  async function exportCurrentSearchReport(): Promise<void> {
    await runTask("导出报告", async () => {
      if (!searchResponse.value?.task_id) return;
      await searchApi.exportReport(searchResponse.value.task_id);
    });
  }

  async function refreshScenicCameraWall(projectId: number, light = false): Promise<void> {
    const ticket = latestRequest.begin("scenic-camera-wall");
    const wall = await scenicApi.cameraWall(projectId, light, ticket.signal).catch(() => null);
    if (!wall || !latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
    scenicCameraWall.value = wall.camera_wall;
  }

  async function refreshScenicCameraPoints(projectId: number): Promise<void> {
    const ticket = latestRequest.begin("scenic-camera-points");
    const points = await scenicApi.cameraPoints(projectId, ticket.signal).catch(() => null);
    if (!points || !latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
    scenicCameraPoints.value = points.camera_points;
  }

  function resetScenicCameraInitialization(): void {
    latestRequest.invalidate("scenic-camera-initialization");
    scenicCameraInitialization = null;
  }

  async function initializeScenicCameras(projectId: number): Promise<void> {
    if (mode.value !== "scenic" || !scenicAccessToken.value || scenicProjectId.value !== projectId
      || scenicUser.value?.role === "viewer" || scenicCameraInitialization?.projectId === projectId) return;
    const initialization = { projectId };
    scenicCameraInitialization = initialization;
    const ticket = latestRequest.begin("scenic-camera-initialization");
    try {
      // Entry only starts configured cameras. Physical discovery opens devices
      // outside the shared capture service and must remain an explicit action.
      const points = await scenicApi.cameraPoints(projectId, ticket.signal);
      if (!latestRequest.isCurrent(ticket)) return;
      if (points.camera_points.some((point) => point.is_active)) {
        await scenicApi.startCameraWall(projectId, "0", ticket.signal);
      }
      if (!latestRequest.isCurrent(ticket)) return;
      await Promise.all([refreshScenicCameraPoints(projectId), refreshScenicCameraWall(projectId, true)]);
    } catch (cause) {
      if (latestRequest.isCurrent(ticket)) {
        if (scenicCameraInitialization === initialization) scenicCameraInitialization = null;
        throw cause;
      }
    }
  }

  async function refreshScenicWorkspace(projectId = scenicProjectId.value): Promise<void> {
    if (!projectId) return;
    const ticket = latestRequest.begin("scenic-workspace");
    const [dashboard, , , , media] = await Promise.all([
      scenicApi.dashboard(projectId, ticket.signal).catch(() => null),
      refreshScenicCameraPoints(projectId),
      refreshScenicCameraWall(projectId),
      loadScenicTourists(),
      mediaApi.list(projectId, "scenic", ticket.signal).catch(() => scenicMediaItems.value),
      initializeScenicCameras(projectId),
    ]);
    if (!latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
    scenicDashboard.value = dashboard;
    scenicMediaItems.value = media;
    mergeSelectedTouristSummary(tourists.value);
  }

  async function syncScenicCameraPoints(): Promise<void> {
    const projectId = scenicProjectId.value;
    await runTask("同步摄像头点位", async () => {
      if (!projectId) return;
      scenicCameraDevices.value = (
        await systemApi.scenicCameras(true).catch(() => ({ devices: [] as CameraDevice[] }))
      ).devices;
      await scenicApi.discoverCameraPoints(projectId);
      if (scenicProjectId.value === projectId) await refreshScenicWorkspace(projectId);
    });
  }

  async function createScenicCameraPoint(name: string, cameraSource: string, role: string, location: string): Promise<void> {
    const projectId = scenicProjectId.value;
    await runTask("新增摄像头点位", async () => {
      if (!projectId) return;
      await scenicApi.createCameraPoint(projectId, { name, camera_source: cameraSource, role, location });
      if (scenicProjectId.value === projectId) await refreshScenicWorkspace(projectId);
    });
  }

  async function setScenicCameraEnabled(source: string, label: string, enabled: boolean): Promise<void> {
    const projectId = scenicProjectId.value;
    await runTask("更新摄像头设置", async () => {
      if (!projectId) return;
      const cameraSource = String(source || "").trim() || "0";
      const point = scenicCameraPoints.value.find((item) => item.camera_source === cameraSource || item.camera_id === cameraSource);
      if (point) {
        await scenicApi.updateCameraPoint(point.id, { is_active: enabled });
      } else if (enabled) {
        await scenicApi.createCameraPoint(projectId, {
          name: label || `摄像头 ${cameraSource}`,
          camera_source: cameraSource,
          role: "play",
          location: "",
        });
      }
      if (enabled) await scenicApi.startCameraWall(projectId);
      if (scenicProjectId.value === projectId) await refreshScenicWorkspace(projectId);
    });
  }

  async function toggleScenicCameraWall(start: boolean): Promise<void> {
    const projectId = scenicProjectId.value;
    await runTask(start ? "启动监控墙" : "停止监控墙", async () => {
      if (!projectId) return;
      if (start) {
        await scenicApi.startCameraWall(projectId);
      } else {
        resetScenicCameraInitialization();
        scenicCameraInitialization = { projectId };
        await scenicApi.stopCameraWall(projectId);
      }
      if (scenicProjectId.value === projectId) await refreshScenicWorkspace(projectId);
    });
  }

  function releaseScenicCameraPreview(): void {
    scenicFramePoll = null;
    scenicPreview = null;
    latestRequest.invalidate("scenic-camera-preview");
  }

  async function prepareScenicCameraPreview(pointId: number, silent = false): Promise<void> {
    const projectId = scenicProjectId.value;
    if (mode.value !== "scenic" || !scenicAccessToken.value || !projectId || !pointId) return;
    const ticket = latestRequest.begin("scenic-camera-preview");
    scenicPreview = { projectId, pointId, refreshedAt: Date.now() };
    try {
      const response = await scenicApi.prepareCameraPreview(projectId, pointId, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || mode.value !== "scenic" || scenicProjectId.value !== projectId) return;
      const index = scenicCameraWall.value.findIndex((item) => (item.id ?? item.point_id) === pointId);
      if (index >= 0) scenicCameraWall.value = scenicCameraWall.value.map((item, itemIndex) => itemIndex === index ? response.camera : item);
      else scenicCameraWall.value = [...scenicCameraWall.value, response.camera];
    } catch (cause) {
      if (latestRequest.isCurrent(ticket) && !silent) {
        error.value = cause instanceof Error ? cause.message : "相机预览启动失败，请重试";
      }
    }
  }

  async function captureScenicSnapshot(pointId?: number): Promise<ScenicSnapshot | null> {
    const projectId = scenicProjectId.value;
    return await runTask("抓拍游客照片", async () => {
      if (!projectId) return null;
      const snapshot = await scenicApi.snapshot(projectId, pointId);
      return scenicProjectId.value === projectId ? snapshot : null;
    });
  }

  async function registerTourist(source: File | ScenicSnapshot | null, ticketCode: string, visitorLabel: string, contactHint: string, consentGranted: boolean): Promise<void> {
    const projectId = scenicProjectId.value;
    await runTask("登记游客", async () => {
      if (!projectId || !source) return;
      const tourist = await scenicApi.registerTourist(projectId, source, {
        ticket_code: ticketCode,
        visitor_label: visitorLabel,
        contact_hint: contactHint,
        consent_granted: consentGranted,
      });
      if (scenicProjectId.value !== projectId) return;
      selectedTourist.value = tourist;
      await refreshScenicWorkspace(projectId);
    });
  }

  async function uploadScenicSimulationVideo(pointId: number, file: File | null): Promise<void> {
    const projectId = scenicProjectId.value;
    await runTask("导入录像", async () => {
      if (!projectId || !pointId || !file) return;
      const task: UploadTask = {
        id: createTaskId(),
        projectId,
        filename: file.name,
        status: "uploading",
        progress: 0.02,
        message: "正在导入录像",
      };
      scenicUploadTasks.value = [task, ...scenicUploadTasks.value].slice(0, 30);
      const response = await scenicApi.uploadSimulationVideo(projectId, pointId, file);
      task.mediaId = response.media_id;
      task.jobId = response.job_id;
      task.status = response.job_id ? "queued" : "succeeded";
      task.progress = response.job_id ? 0.08 : 1;
      task.message = response.job_id ? "等待特征提取" : "上传完成";
      scenicUploadTasks.value = [...scenicUploadTasks.value];
      if (scenicProjectId.value === projectId) await refreshWorkspace("scenic");
    });
  }

  async function selectTourist(sessionId: number): Promise<void> {
    const projectId = scenicProjectId.value;
    const ticket = latestRequest.begin("selected-tourist");
    await runTask("读取游客详情", async () => {
      const tourist = await scenicApi.tourist(sessionId, ticket.signal);
      if (!latestRequest.isCurrent(ticket) || scenicProjectId.value !== projectId) return;
      selectedTourist.value = tourist;
    });
  }

  async function matchSelectedTourist(minSimilarity: number): Promise<void> {
    const sessionId = selectedTourist.value?.id || 0;
    await runTask("跨摄像头匹配", async () => {
      if (!sessionId) return;
      const response = await scenicApi.matchTourist(sessionId, minSimilarity);
      if (selectedTourist.value?.id !== sessionId) return;
      selectedTourist.value = response.tourist;
      await refreshScenicWorkspace();
    });
  }

  async function updateSegment(segmentId: number, included: boolean, sortOrder: number): Promise<void> {
    const sessionId = selectedTourist.value?.id || 0;
    await runTask("更新片段", async () => {
      if (!sessionId) return;
      await scenicApi.updateSegment(sessionId, segmentId, included, sortOrder);
      if (selectedTourist.value?.id === sessionId) await selectTourist(sessionId);
    });
  }

  async function updateSegments(segmentIds: number[], included: boolean): Promise<void> {
    const sessionId = selectedTourist.value?.id || 0;
    await runTask("批量更新片段", async () => {
      if (!sessionId || !segmentIds.length) return;
      await scenicApi.updateSegments(sessionId, segmentIds, included);
      if (selectedTourist.value?.id === sessionId) await selectTourist(sessionId);
    });
  }

  async function generateVlog(): Promise<void> {
    const sessionId = selectedTourist.value?.id || 0;
    await runTask("提交成片任务", async () => {
      if (!sessionId) return;
      await scenicApi.generateVlog(sessionId);
      if (selectedTourist.value?.id === sessionId) await selectTourist(sessionId);
    });
  }

  async function createScenicOrder(amountCents: number, vlogJobId?: number): Promise<void> {
    const sessionId = selectedTourist.value?.id || 0;
    await runTask("创建交付订单", async () => {
      if (!sessionId) return;
      const order = await scenicApi.createOrder(sessionId, amountCents, vlogJobId);
      if (selectedTourist.value?.id === sessionId) mergeSelectedTouristOrder(order);
    });
  }

  async function createWechatPayment(amountCents: number, vlogJobId?: number): Promise<ScenicOrder | null> {
    const sessionId = selectedTourist.value?.id || 0;
    return await runTask("创建支付订单", async () => {
      if (!sessionId) return null;
      const order = await scenicApi.createWechatOrder(sessionId, amountCents, vlogJobId);
      if (selectedTourist.value?.id === sessionId) mergeSelectedTouristOrder(order);
      return order;
    });
  }

  async function completeSimulatedPayment(orderId: number, result: "success" | "failed"): Promise<ScenicOrder | null> {
    const sessionId = selectedTourist.value?.id || 0;
    return await runTask(result === "success" ? "确认支付成功" : "取消支付", async () => {
      if (!sessionId) return null;
      const order = await scenicApi.completeSimulatedPayment(orderId, result);
      if (selectedTourist.value?.id === sessionId) mergeSelectedTouristOrder(order);
      return order;
    });
  }

  async function refreshScenicOrderPayment(orderId: number, silent = false): Promise<ScenicOrder | null> {
    const sessionId = selectedTourist.value?.id || 0;
    const refresh = async (): Promise<ScenicOrder | null> => {
      if (!sessionId) return null;
      const order = await scenicApi.refreshOrderPayment(orderId);
      if (selectedTourist.value?.id === sessionId) mergeSelectedTouristOrder(order);
      return order;
    };
    if (!silent) return await runTask("刷新支付状态", refresh);
    try {
      return await refresh();
    } catch (refreshError) {
      console.warn("refreshScenicOrderPayment:", refreshError);
      return null;
    }
  }

  async function markScenicOrderPaid(orderId: number): Promise<void> {
    const sessionId = selectedTourist.value?.id || 0;
    await runTask("确认订单支付", async () => {
      if (!sessionId) return;
      const order = await scenicApi.markOrderPaid(orderId);
      if (selectedTourist.value?.id === sessionId) mergeSelectedTouristOrder(order);
    });
  }

  function jobTitle(item: unknown): string {
    return itemText(item, "type") || itemText(item, "job_type") || itemText(item, "id") || "任务";
  }

  function jobStatus(item: unknown): string {
    return itemText(item, "status") || itemText(item, "message") || "unknown";
  }

  return {
    mode,
    busy,
    message,
    error,
    readyz,
    metrics,
    observability,
    cameraDevices,
    scenicCameraDevices,
    coreAccessToken,
    scenicAccessToken,
    coreUser,
    scenicUser,
    preferences,
    coreLogin,
    scenicLogin,
    coreProjects,
    scenicProjects,
    coreProjectId,
    scenicProjectId,
    coreProjectStatus,
    scenicProjectStatus,
    coreCameraStatus,
    mediaItems,
    scenicMediaItems,
    previewUrls,
    jobs,
    uploadTasks,
    auditRows,
    searchResults,
    searchResponse,
    searchHistory,
    selectedResult,
    scenicDashboard,
    scenicCameraPoints,
    scenicCameraWall,
    tourists,
    touristQuery, touristPagination, touristQueueCounts, touristLoading, touristError,
    scenicHistoryQuery, scenicHistoryPagination, scenicHistoryLoading, scenicHistoryError,
    scenicAuditQuery, scenicAuditPagination, scenicAuditLoading, scenicAuditError,
    loadScenicTourists,
    selectedTourist,
    activeToken,
    isAuthenticated,
    activeUser,
    activeProjects,
    activeProjectId,
    activeProject,
    activeStatus,
    activeIsCameraProject,
    activeJobs,
    systemReady,
    setMode,
    setProject,
    bootstrap,
    refreshSystem,
    updatePreferences,
    loadAuditLogs,
    login,
    register,
    requestEmailCode,
    logout,
    loadProjects,
    createProject,
    refreshWorkspace,
    uploadMedia,
    rebuildIndex,
    refreshCoreCameraStatus,
    startCoreCamera,
    stopCoreCamera,
    pollLiveFeeds,
    runTextSearch,
    runImageSearch,
    loadSearchHistory,
    loadHistoryPage,
    exportCurrentSearchReport,
    syncScenicCameraPoints,
    setScenicCameraEnabled,
    captureScenicSnapshot,
    prepareScenicCameraPreview,
    releaseScenicCameraPreview,
    createScenicCameraPoint,
    toggleScenicCameraWall,
    registerTourist,
    uploadScenicSimulationVideo,
    selectTourist,
    matchSelectedTourist,
    updateSegment,
    updateSegments,
    generateVlog,
    createScenicOrder,
    createWechatPayment,
    completeSimulatedPayment,
    refreshScenicOrderPayment,
    markScenicOrderPaid,
    jobTitle,
    jobStatus,
  };
});
