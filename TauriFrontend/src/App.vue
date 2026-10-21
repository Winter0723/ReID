<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import {
  Activity,
  AlertTriangle,
  Boxes,
  Camera,
  CheckCircle2,
  Cpu,
  Database,
  Download,
  FileImage,
  Film,
  FolderPlus,
  History,
  ImageUp,
  KeyRound,
  Play,
  Route,
  Search,
  Server,
  Settings2,
  Sparkles,
  Upload,
  X,
} from "@lucide/vue";
import { downloadAsset, normalizeAssetUrl } from "./lib/api/http";
import type { SearchFilters, SearchResult } from "./lib/api/search";
import { resolveVideoPreviewSource } from "./lib/videoPreview";
import LiveCameraPreview from "./components/LiveCameraPreview.vue";
import ScenicApp from "./components/ScenicApp.vue";
import MediaPreviewDialog from "./components/MediaPreviewDialog.vue";
import CoreAccountDialog from "./components/core/CoreAccountDialog.vue";
import CoreAuditView from "./components/core/CoreAuditView.vue";
import CoreRealtimeView from "./components/core/CoreRealtimeView.vue";
import CoreHistoryView from "./components/core/CoreHistoryView.vue";
import CoreProjectDialog from "./components/core/CoreProjectDialog.vue";
import CoreSearchResultsView from "./components/core/CoreSearchResultsView.vue";
import { useWorkbenchStore } from "./stores/workbench";

const store = useWorkbenchStore();
const isScenicMode = computed(() => store.mode === "scenic");
const showLoginModal = ref(false);
const showProjectModal = ref(false);
const activeView = ref<"workspace" | "results" | "audit" | "history" | "realtime">("workspace");

const coreCameraForm = reactive({ source: "0" });
const searchForm = reactive({ text: "", minSimilarity: -1, cameraId: "", startTime: "", endTime: "", mediaId: 0 });

const mediaFiles = ref<File[]>([]);
const searchImageFile = ref<File | null>(null);
const searchImagePreviewUrl = ref("");
const lastSearchKind = ref<"text" | "image" | "">("");
const mediaPreview = reactive({
  open: false,
  title: "",
  type: "image" as "image" | "video",
  source: "",
  frameSource: "",
  downloadSource: "",
  fallbackSource: "",
  downloadName: "",
  seekSeconds: 0,
  fallbackSeekSeconds: 0,
  error: "",
});

const coreMonitorOnline = computed(() => Boolean(store.coreCameraStatus?.monitor_running || store.coreCameraStatus?.latest_frame_url));
const coreIngesting = computed(() => Boolean(store.coreCameraStatus?.running));

const queueStatus = computed(() => {
  if (store.readyz?.queue_mode === "local") return "本地处理";
  if (store.readyz?.queue_ready === true) return "就绪";
  if (store.readyz?.queue_ready === false) return "不可用";
  return "未知";
});

const readyStatus = computed(() => ({
  ready: "就绪",
  degraded: "降级",
  warming: "预热",
  unhealthy: "异常",
  offline: "离线",
}[store.readyz?.status || ""] || "未知"));

const coreCameraPreviewKey = computed(() => `${store.coreProjectId}:${
  store.coreCameraStatus?.monitor_source || store.coreCameraStatus?.camera_source || store.activeProject?.camera_source || ""
}`);
const coreCameraFrameUrl = computed(() => {
  const monitor = store.coreCameraStatus?.monitor_status;
  const monitorFrameUrl = typeof monitor?.latest_frame_url === "string" ? monitor.latest_frame_url : "";
  const monitorFrameVersion = Number(monitor?.latest_frame_version || 0);
  const frameUrl = monitorFrameUrl || store.coreCameraStatus?.latest_frame_url;
  if (!frameUrl) return "";
  const normalized = normalizeAssetUrl(frameUrl);
  const version = monitorFrameVersion || store.coreCameraStatus?.latest_frame_version || 0;
  return `${normalized}${normalized.includes("?") ? "&" : "?"}v=${version}`;
});

let liveFeedTimer: number | undefined;

function restartLiveFeedTimer(): void {
  if (liveFeedTimer !== undefined) window.clearInterval(liveFeedTimer);
  liveFeedTimer = undefined;
  liveFeedTimer = window.setInterval(() => {
    void store.pollLiveFeeds();
  }, Math.min(store.preferences.livePollIntervalMs, 250));
}

onMounted(() => {
  void store.bootstrap();
  restartLiveFeedTimer();
});

watch(
  () => [store.preferences.autoRefresh, store.preferences.livePollIntervalMs],
  restartLiveFeedTimer,
);

watch(
  () => [store.mode, store.isAuthenticated, store.error],
  ([currentMode, authenticated, currentError]) => {
    if (currentMode === "core" && !authenticated && String(currentError).includes("登录状态已过期")) {
      showLoginModal.value = true;
    }
  },
);

onUnmounted(() => {
  if (liveFeedTimer !== undefined) window.clearInterval(liveFeedTimer);
  if (searchImagePreviewUrl.value) URL.revokeObjectURL(searchImagePreviewUrl.value);
});

function selectFiles(event: Event, target: "media" | "search"): void {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  if (target === "media") mediaFiles.value = files;
  if (target === "search") {
    if (searchImagePreviewUrl.value) URL.revokeObjectURL(searchImagePreviewUrl.value);
    searchImageFile.value = files[0] || null;
    searchImagePreviewUrl.value = searchImageFile.value ? URL.createObjectURL(searchImageFile.value) : "";
  }
}

function searchFilters(): SearchFilters {
  return {
    min_similarity: searchForm.minSimilarity,
    camera_id: searchForm.cameraId,
    start_time: searchForm.startTime,
    end_time: searchForm.endTime,
    media_id: searchForm.mediaId || undefined,
  };
}

async function runTextSearch(): Promise<void> {
  if (!store.activeProjectId || !searchForm.text) return;
  const previousResponse = store.searchResponse;
  await store.runTextSearch(searchForm.text, searchFilters());
  if (store.searchResponse !== previousResponse) {
    lastSearchKind.value = "text";
    activeView.value = "results";
  }
}

async function runImageSearch(): Promise<void> {
  if (!store.activeProjectId || !searchImageFile.value) return;
  const previousResponse = store.searchResponse;
  await store.runImageSearch(searchImageFile.value, searchFilters());
  if (store.searchResponse !== previousResponse) {
    lastSearchKind.value = "image";
    activeView.value = "results";
  }
}

function sourceUrl(result: SearchResult): string {
  return normalizeAssetUrl(result.original_media_url || result.original_media_path || result.thumbnail_url || result.path);
}

function fileNameFromUrl(value?: string, fallback = "myreid_media"): string {
  const clean = String(value || "").split("#")[0].split("?")[0];
  const name = clean.split(/[\\/]/).pop() || fallback;
  try {
    return decodeURIComponent(name).replace(/[\\/:*?"<>|]/g, "_") || fallback;
  } catch {
    return name.replace(/[\\/:*?"<>|]/g, "_") || fallback;
  }
}

function isVideoSource(mediaType?: string, source?: string): boolean {
  const text = `${mediaType || ""} ${source || ""}`.toLowerCase();
  return text.includes("video") || text.includes("camera") || /\.(mp4|mov|avi|mkv|webm)(\?|#|$)/i.test(text);
}

function resultSeekSeconds(result: SearchResult): number {
  return Number(result.video_seek_seconds ?? result.timestamp_seconds ?? 0) || 0;
}

function openMediaPreview(payload: {
  title: string;
  source?: string;
  mediaType?: string;
  frameSource?: string;
  fallbackSource?: string;
  seekSeconds?: number;
  fallbackSeekSeconds?: number;
  downloadSource?: string;
  downloadName?: string;
}): void {
  const source = normalizeAssetUrl(payload.source || "");
  if (!source) return;
  mediaPreview.title = payload.title;
  mediaPreview.source = source;
  mediaPreview.frameSource = normalizeAssetUrl(payload.frameSource || "");
  mediaPreview.downloadSource = normalizeAssetUrl(payload.downloadSource || payload.source || "");
  mediaPreview.fallbackSource = normalizeAssetUrl(payload.fallbackSource || payload.downloadSource || payload.source || "");
  mediaPreview.downloadName = payload.downloadName || fileNameFromUrl(payload.downloadSource || payload.source, "myreid_media");
  mediaPreview.seekSeconds = Math.max(0, Number(payload.seekSeconds || 0));
  mediaPreview.fallbackSeekSeconds = Math.max(0, Number(payload.fallbackSeekSeconds ?? payload.seekSeconds ?? 0));
  mediaPreview.error = "";
  mediaPreview.type = isVideoSource(payload.mediaType, source) ? "video" : "image";
  mediaPreview.open = true;
}

function isVideoResult(result: SearchResult): boolean {
  return isVideoSource(
    result.media_type,
    result.original_media_url || result.original_media_path || result.media_filename,
  );
}

function downloadSearchResult(result: SearchResult): void {
  const source = sourceUrl(result);
  void downloadCoreAsset(source, result.media_filename || fileNameFromUrl(source));
}

function previewResultFrame(result: SearchResult): void {
  openMediaPreview({
    title: isVideoResult(result)
      ? `命中帧 #${result.rank || result.id || "-"}`
      : (result.media_filename || `命中图片 #${result.rank || result.id || "-"}`),
    source: result.thumbnail_url || result.path,
    mediaType: "image",
    downloadName: `myreid_result_${result.id || result.rank || "frame"}.jpg`,
  });
}

async function previewResultMedia(result: SearchResult, seekToHit: boolean): Promise<void> {
  const source = result.original_media_url || result.original_media_path || result.thumbnail_url || result.path;
  const hasOriginalMedia = Boolean(result.original_media_url || result.original_media_path);
  const canPreviewVideo = hasOriginalMedia && isVideoSource(result.media_type, source);
  const hitSeconds = resultSeekSeconds(result);
  const startSeconds = seekToHit ? Math.max(0, hitSeconds - 2) : 0;
  const preview = canPreviewVideo
    ? await resolveVideoPreviewSource({
      scope: "core",
      projectId: store.activeProjectId,
      mediaId: result.media_id,
      fallback: source,
      full: !seekToHit,
      startSeconds,
      durationSeconds: seekToHit ? 8 : undefined,
      generatedSeekSeconds: seekToHit ? Math.max(0, hitSeconds - startSeconds) : 0,
      fallbackSeekSeconds: seekToHit ? hitSeconds : 0,
    })
    : {
      source,
      fallbackSource: source,
      downloadSource: source,
      seekSeconds: 0,
      fallbackSeekSeconds: 0,
      generated: false,
    };
  openMediaPreview({
    title: seekToHit ? "视频帧位置" : (result.media_filename || "完整媒体"),
    source: preview.source,
    mediaType: canPreviewVideo ? result.media_type : "image",
    frameSource: result.thumbnail_url || result.path,
    fallbackSource: preview.fallbackSource,
    seekSeconds: preview.seekSeconds,
    fallbackSeekSeconds: preview.fallbackSeekSeconds,
    downloadSource: preview.downloadSource || result.original_media_url || result.original_media_path || source,
    downloadName: result.media_filename || fileNameFromUrl(source),
  });
}

function progressPercent(value?: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value || 0)) * 100)}%`;
}

function taskTone(status?: string): string {
  if (status === "failed") return "bg-rose-400";
  if (status === "succeeded" || status === "duplicate") return "bg-emerald-400";
  if (status === "running") return "bg-cyan-400";
  return "bg-amber-400";
}

function jobProgress(job: { progress?: number }): number {
  return Math.max(0, Math.min(1, Number(job.progress || 0)));
}

function jobMessage(job: { message?: string; error?: string | null }): string {
  return job.error || job.message || "等待处理";
}

function mediaName(): string {
  if (!mediaFiles.value.length) return "选择媒体";
  if (mediaFiles.value.length === 1) return mediaFiles.value[0].name;
  return `${mediaFiles.value.length} 个文件`;
}

async function downloadCoreAsset(source: string, filename: string): Promise<void> {
  if (!source) return;
  try {
    await downloadAsset(source, filename || "myreid-media", "core");
  } catch (error) {
    store.error = error instanceof Error ? error.message : "下载失败";
  }
}

</script>

<template>
  <ScenicApp v-if="isScenicMode" @switch-core="store.setMode('core')" />
  <main v-else class="core-shell w-screen h-screen flex bg-[#070809] text-zinc-100 overflow-hidden relative">
    <div class="ambient-grid"></div>
    <div class="scanline"></div>

    <div v-if="activeView === 'realtime' && (store.error || store.message)" class="pointer-events-none fixed bottom-4 left-20 right-4 z-40 rounded-xl border border-white/10 bg-zinc-950/95 px-4 py-3 text-sm shadow-xl" :class="store.error ? 'text-rose-300' : 'text-cyan-200'" :role="store.error ? 'alert' : 'status'" aria-live="polite">
      {{ store.error || store.message }}
    </div>
    <aside class="app-sidebar core-sidebar">
      <button class="app-brand" type="button" aria-label="打开核心工作台" @click="activeView = 'workspace'"><span class="brand-symbol"><Sparkles :size="21" /></span><span><strong>MyReID</strong><small>VISION WORKSPACE</small></span></button>
      <button class="workspace-switch" type="button" title="切换到景区运营" aria-label="切换到景区运营" @click="store.setMode('scenic')">核心 ReID 工作台 <Route :size="14" /></button>
      <p class="nav-caption">检索工作区</p>
      <nav class="rail-nav" aria-label="核心导航">
        <button class="rail-button" :class="{ active: activeView === 'results' }" type="button" title="检索结果" aria-label="打开检索结果" @click="activeView = 'results'"><Search :size="17" /><span>跨模态检索</span></button>
        <button class="rail-button" :class="{ active: activeView === 'workspace' }" type="button" aria-label="媒体资源" @click="activeView = 'workspace'"><Database :size="17" /><span>媒体资源</span></button>
        <button class="rail-button" :class="{ active: activeView === 'realtime' }" type="button" title="实时行人识别" aria-label="实时行人识别" @click="activeView = activeView === 'realtime' ? 'workspace' : 'realtime'"><Camera :size="17" /><span>实时采集</span></button>
        <button class="rail-button" :class="{ active: activeView === 'history' }" type="button" title="搜索记录与历史任务" aria-label="打开搜索记录与历史任务" @click="activeView = 'history'"><History :size="17" /><span>检索历史</span></button>
      </nav>
      <p class="nav-caption">管理</p>
      <nav class="management-nav" aria-label="核心管理">
        <button class="rail-button" type="button" :title="`项目: ${store.activeProject?.name || '未选择'}`" :aria-label="`打开项目管理：${store.activeProject?.name || '未选择项目'}`" @click="showProjectModal = true"><FolderPlus :size="17" /><span>项目管理</span></button>
        <button class="rail-button" type="button" aria-label="核心账号与权限" @click="showLoginModal = true"><KeyRound :size="17" /><span>账号与权限</span></button>
        <button class="rail-button" :class="{ active: activeView === 'audit' }" type="button" title="审计日志与系统事件" aria-label="打开审计日志与系统事件" @click="activeView = 'audit'"><Activity :size="17" /><span>审计日志</span></button>
      </nav>
      <div class="sidebar-footer"><button class="system-health" type="button" title="刷新系统状态" aria-label="刷新系统状态" @click="store.refreshSystem()"><span><i :class="{ online: store.systemReady }"></i>服务{{ readyStatus }}</span><small>队列 {{ queueStatus }}</small><small>核心工作台 · v1.0</small></button>
        <div class="sidebar-account"><button type="button" :aria-label="store.isAuthenticated ? `打开账户信息：${store.activeUser?.username || '当前用户'}` : '打开登录窗口'" @click="showLoginModal = true"><Settings2 :size="18" /><span>{{ store.activeUser?.username || '登录工作台' }}<small>核心 ReID</small></span></button></div>
      </div>
    </aside>

    <!-- MAIN WORKSPACE CONTAINER -->
    <div class="core-main flex-1 flex min-w-0 h-screen relative z-10">
      <div class="workspace-topbar"><button type="button" @click="showProjectModal = true"><FolderPlus :size="16" />{{ store.activeProject?.name || '选择项目' }} / 核心工作区</button><div><span class="workspace-tag">核心工作台</span><button type="button" @click="store.setMode('scenic')">切换工作台</button></div></div>
      <header class="workspace-page-heading"><div><h1>{{ activeView === 'results' ? '跨模态检索' : activeView === 'workspace' ? '媒体资源' : activeView === 'realtime' ? '实时采集' : activeView === 'history' ? '检索历史' : '审计日志' }}</h1><p>连接每一个画面，从海量影像中找到目标。</p></div></header>
      <div class="core-columns">
      
      <!-- 2. MIDDLE COLUMN (Control Panel) -->
      <section v-if="activeView === 'workspace' || activeView === 'results'" class="core-control-panel w-[35%] shrink-0 flex flex-col border-r border-white/[0.06] bg-zinc-950/20 backdrop-blur-sm relative h-full">
        <header class="h-[72px] shrink-0 border-b border-white/[0.06] px-6 flex items-center justify-between bg-zinc-900/10">
          <div>
            <p class="text-[10px] uppercase tracking-[0.2em] text-zinc-500 mb-0.5">Control Panel</p>
            <h2 class="text-sm font-bold text-white tracking-tight">核心中控</h2>
          </div>
          <div class="status-chip text-xs h-8 px-3">
            <CheckCircle2 v-if="store.systemReady" :size="13" />
            <AlertTriangle v-else :size="13" />
            <span>{{ readyStatus }}</span>
          </div>
        </header>
        
        <div v-if="store.error || store.message" class="shrink-0 border-b border-white/10 px-6 py-3 text-xs break-words" :class="store.error ? 'text-rose-300' : 'text-cyan-200'" :role="store.error ? 'alert' : 'status'" aria-live="polite">
          {{ store.error || store.message }}
        </div>
        <div class="control-sections flex-1 overflow-y-auto p-6 space-y-6">
          <div v-if="store.isAuthenticated && !store.coreProjectId" class="rounded-xl border border-amber-400/20 bg-amber-400/[0.07] p-4" role="status">
            <div class="flex items-start gap-3">
              <FolderPlus class="mt-0.5 shrink-0 text-amber-300" :size="18" />
              <div class="min-w-0">
                <strong class="block text-sm text-white">请先创建或选择项目</strong>
                <p class="mt-1 text-xs leading-5 text-zinc-400">完成项目选择后，才能上传媒体、执行检索或重建索引。</p>
                <button class="ghost-button mt-3 min-h-8 rounded-lg px-3 py-1.5 text-xs" type="button" @click="showProjectModal = true">打开项目管理</button>
              </div>
            </div>
          </div>
          <!-- CORE MODE: Vector DB & Search -->
            <div class="glass-panel p-5 ingestion-panel">
              <div class="mb-4 flex items-center justify-between">
                <div>
                  <p class="eyebrow text-cyan-300">向量数据库</p>
                  <h2 class="panel-title text-base">状态与入库</h2>
                </div>
                <button class="icon-button w-8 h-8 rounded-lg" type="button" :title="store.coreProjectId ? '重建索引' : '请先创建或选择项目'" aria-label="重建当前项目索引" :disabled="!store.coreProjectId" @click="store.rebuildIndex()">
                  <Settings2 :size="14" />
                </button>
              </div>

              <div class="grid gap-3 grid-cols-3">
                <div class="signal-card bg-zinc-900/40 p-3 rounded-lg border border-white/[0.06]">
                  <Cpu :size="16" class="text-cyan-400 mb-2" />
                  <span class="text-[10px] text-zinc-500 block">模型状态</span>
                  <strong class="text-[11px] leading-tight whitespace-nowrap text-white mt-0.5 block">{{ store.readyz?.clip_loaded ? '就绪' : '等待' }}</strong>
                </div>
                <div class="signal-card bg-zinc-900/40 p-3 rounded-lg border border-white/[0.06]">
                  <Boxes :size="16" class="text-emerald-400 mb-2" />
                  <span class="text-[10px] text-zinc-500 block">目标规模</span>
                  <strong class="text-[11px] leading-tight whitespace-nowrap text-white mt-0.5 block">{{ store.activeStatus?.indexed_targets ?? 0 }}</strong>
                </div>
                <div class="signal-card bg-zinc-900/40 p-3 rounded-lg border border-white/[0.06]">
                  <Server :size="16" class="text-rose-400 mb-2" />
                  <span class="text-[10px] text-zinc-500 block">队列状态</span>
                  <strong class="text-[11px] leading-tight whitespace-nowrap text-white mt-0.5 block" :title="queueStatus">{{ queueStatus }}</strong>
                </div>
              </div>
              
              <div class="mt-4 grid gap-2">
                <label class="file-drop rounded-xl flex items-center justify-center gap-2 cursor-pointer py-3 border border-dashed border-white/10 hover:border-white/20 transition-colors bg-zinc-900/20">
                  <Upload :size="16" class="text-cyan-400" />
                  <span class="text-xs text-zinc-300">{{ mediaName() }}</span>
                  <input class="hidden" multiple type="file" accept="image/*,video/*" aria-label="选择待上传的图片或视频" @change="selectFiles($event, 'media')" />
                </label>
                <button class="primary-button rounded-xl py-2.5 text-xs min-h-[38px] justify-center" type="button" :disabled="!mediaFiles.length || !store.coreProjectId" :title="!store.coreProjectId ? '请先创建或选择项目' : (!mediaFiles.length ? '请先选择媒体文件' : '上传并提取特征')" @click="store.uploadMedia(mediaFiles)">
                  <Upload :size="14" />
                  <span>上传并提取特征</span>
                </button>
              </div>

              <div v-if="store.uploadTasks.length || store.activeJobs.length" class="mt-4 space-y-2">
                <div v-for="task in store.uploadTasks.slice(0, 5)" :key="task.id" class="rounded-lg border border-white/[0.06] bg-zinc-900/40 p-3">
                  <div class="flex items-center justify-between gap-3">
                    <span class="min-w-0 truncate text-xs font-semibold text-white">{{ task.filename }}</span>
                    <span class="shrink-0 text-[10px] text-zinc-400">{{ progressPercent(task.progress) }}</span>
                  </div>
                  <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                    <div class="h-full rounded-full transition-all" :class="taskTone(task.status)" :style="{ width: progressPercent(task.progress) }"></div>
                  </div>
                  <p class="mt-2 truncate text-[10px]" :class="task.error ? 'text-rose-300' : 'text-zinc-500'">{{ task.error || task.message }}</p>
                </div>

                <div v-for="job in store.activeJobs.slice(0, 3)" :key="job.id" class="rounded-lg border border-white/[0.06] bg-zinc-950/40 p-3">
                  <div class="flex items-center justify-between gap-3">
                    <span class="min-w-0 truncate text-xs text-zinc-300">{{ store.jobTitle(job) }}</span>
                    <span class="shrink-0 text-[10px] text-zinc-400">{{ progressPercent(jobProgress(job)) }}</span>
                  </div>
                  <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                    <div class="h-full rounded-full bg-cyan-400 transition-all" :style="{ width: progressPercent(jobProgress(job)) }"></div>
                  </div>
                  <p class="mt-2 truncate text-[10px] text-zinc-500">{{ jobMessage(job) }}</p>
                </div>
              </div>
            </div>

            <div v-if="store.activeIsCameraProject" class="glass-panel p-5">
              <div class="mb-4 flex items-center justify-between">
                <div>
                  <p class="eyebrow text-amber-300">实时相机</p>
                  <h2 class="panel-title text-base">采集与入库</h2>
                </div>
                <Camera class="text-amber-400" :size="18" />
              </div>

              <div class="space-y-3">
                <select v-if="store.cameraDevices.length" v-model="coreCameraForm.source" class="field text-xs rounded-xl bg-zinc-900/50 border-white/[0.06]">
                  <option v-for="device in store.cameraDevices" :key="String(device.source)" :value="String(device.source)">
                    {{ device.label || device.name || `摄像头 ${device.source}` }}
                  </option>
                </select>
                <input v-model="coreCameraForm.source" class="field text-xs rounded-xl bg-zinc-900/50 border-white/[0.06]" placeholder="设备 ID，如 0 或 entrance" aria-label="相机源" />
                <p class="text-xs leading-relaxed text-zinc-400">请选择管理员授权的摄像头，或填写配置中的设备 ID。远程连接地址由管理员配置。</p>
                <button class="ghost-button text-xs" type="button" :disabled="store.busy" @click="store.refreshSystem(true)">刷新摄像头列表</button>
                <button
                  class="primary-button text-xs rounded-xl py-2.5 min-h-[38px] justify-center w-full"
                  type="button"
                  @click="coreIngesting ? store.stopCoreCamera() : store.startCoreCamera(coreCameraForm.source)"
                >
                  <Play v-if="!coreIngesting" :size="14" />
                  <X v-else :size="14" />
                  <span>{{ coreIngesting ? '暂停实时入库' : '启用实时入库' }}</span>
                </button>
                <div
                  class="rounded-xl border border-white/[0.06] bg-zinc-900/40 p-3 text-xs"
                  :class="store.coreCameraStatus?.error ? 'text-rose-300' : 'text-zinc-400'"
                >
                  {{ store.coreCameraStatus?.error || store.coreCameraStatus?.message || (store.coreCameraStatus?.running ? '相机运行中' : '相机未启动') }}
                </div>
              </div>
            </div>

            <div class="glass-panel p-5 query-form">
              <div class="mb-4 flex items-center justify-between">
                <div>
                  <p class="eyebrow text-purple-300">检索中枢</p>
                  <h2 class="panel-title text-base">查找目标</h2>
                </div>
                <Search class="text-purple-400" :size="18" />
              </div>

              <div class="space-y-3">
                <textarea v-model="searchForm.text" class="field min-h-24 resize-none rounded-xl text-sm bg-zinc-900/50 border-white/[0.06]" placeholder="描述目标人物的衣着、外貌等特征"></textarea>
                <div class="grid gap-2 grid-cols-2">
                  <input v-model.number="searchForm.minSimilarity" class="field text-sm rounded-xl bg-zinc-900/50 border-white/[0.06]" max="1" min="-1" step="0.05" type="number" />
                  <input v-model="searchForm.cameraId" class="field text-sm rounded-xl bg-zinc-900/50 border-white/[0.06]" placeholder="摄像头 ID" />
                  <input v-model="searchForm.startTime" class="field text-sm rounded-xl bg-zinc-900/50 border-white/[0.06]" placeholder="开始时间" />
                  <input v-model="searchForm.endTime" class="field text-sm rounded-xl bg-zinc-900/50 border-white/[0.06]" placeholder="结束时间" />
                </div>
                <label class="file-drop rounded-xl flex items-center justify-center gap-2 cursor-pointer py-3 border border-dashed border-white/10 hover:border-white/20 transition-colors bg-zinc-900/20">
                  <ImageUp :size="16" class="text-purple-400" />
                  <span class="text-xs text-zinc-300 truncate">{{ searchImageFile?.name || '选择检索图片' }}</span>
                  <input class="hidden" type="file" accept="image/*" aria-label="选择用于检索的图片" @change="selectFiles($event, 'search')" />
                </label>
                <div class="search-action-grid grid grid-cols-2 gap-2 mt-2">
                  <button class="primary-button text-xs rounded-xl py-2.5 min-h-[38px] justify-center" type="button" :disabled="!store.coreProjectId || !searchForm.text.trim()" :title="!store.coreProjectId ? '请先创建或选择项目' : (!searchForm.text.trim() ? '请输入检索文本' : '执行文本检索')" @click="runTextSearch">
                    <Search :size="14" />
                    <span>以文搜图</span>
                  </button>
                  <button class="ghost-button text-xs rounded-xl py-2.5 min-h-[38px] justify-center bg-zinc-900/40 border-white/[0.06]" type="button" :disabled="!store.coreProjectId || !searchImageFile" :title="!store.coreProjectId ? '请先创建或选择项目' : (!searchImageFile ? '请选择检索图片' : '执行图片检索')" @click="runImageSearch">
                    <FileImage :size="14" />
                    <span>以图搜图</span>
                  </button>
                </div>
              </div>
            </div>
        </div>
      </section>

      <!-- 3. RIGHT COLUMN (Result Workspace) -->
      <section class="core-result-panel flex-1 flex flex-col min-w-0 bg-[#0A0B0C] relative h-full">
        <template v-if="activeView === 'workspace'">
          <header class="h-[72px] shrink-0 border-b border-white/[0.06] px-8 flex items-center justify-between">
            <div>
              <p class="text-[10px] uppercase tracking-[0.2em] text-zinc-500 mb-0.5">Live Workspace</p>
              <h1 class="text-lg font-bold tracking-tight text-white/90">
                实时监控与媒体
              </h1>
            </div>
          </header>

        <div class="flex-1 overflow-y-auto p-8 space-y-8">
          <!-- CORE MODE: Waterfall & Portrait -->
            <div v-if="store.activeIsCameraProject" class="glass-panel p-4">
              <div class="mb-4 flex items-center justify-between">
                <h3 class="text-sm font-semibold text-white flex items-center gap-2">
                  <Camera class="text-amber-400" :size="16" />
                  实时相机预览
                </h3>
                <span class="text-[10px] font-bold px-2 py-1 rounded border" :class="coreMonitorOnline ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : 'bg-zinc-900 text-zinc-500 border-zinc-700'">{{ coreMonitorOnline ? '监控在线' : '等待监控' }}</span>
              </div>
              <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_220px]">
                <div class="relative overflow-hidden rounded-xl bg-zinc-950 border border-white/[0.06] aspect-video">
                  <LiveCameraPreview v-if="coreCameraFrameUrl" :key="coreCameraPreviewKey" :src="coreCameraFrameUrl" :stream-endpoint="`/api/v1/projects/${store.coreProjectId}/camera/preview-stream`" class="absolute inset-0 w-full h-full" alt="摄像头实时预览" />
                  <div v-else class="absolute inset-0 flex flex-col items-center justify-center text-zinc-600">
                    <Camera :size="28" />
                    <span class="mt-2 text-xs">等待相机画面</span>
                  </div>
                </div>
                <div class="grid gap-3">
                  <div class="bg-zinc-900/40 p-3 rounded-xl border border-white/[0.06]">
                    <span class="text-[10px] text-zinc-500 block">相机源</span>
                    <strong class="text-sm text-white mt-1 block truncate">{{ store.coreCameraStatus?.camera_source || store.activeProject?.camera_source || '-' }}</strong>
                    <small v-if="store.coreCameraStatus?.monitor_status?.width" class="text-zinc-400">{{ store.coreCameraStatus.monitor_status.width }}×{{ store.coreCameraStatus.monitor_status.height }} · 预览源 {{ store.coreCameraStatus.monitor_status.preview_fps || 0 }} FPS</small>
                  </div>
                  <div class="bg-zinc-900/40 p-3 rounded-xl border border-white/[0.06]">
                    <span class="text-[10px] text-zinc-500 block">已索引目标</span>
                    <strong class="text-lg text-cyan-400 mt-1 block">{{ store.coreCameraStatus?.indexed_targets ?? 0 }}</strong>
                  </div>
                  <button v-if="store.coreCameraStatus?.recording_url" class="ghost-button text-xs rounded-xl justify-center" type="button" @click="downloadCoreAsset(store.coreCameraStatus.recording_url, store.coreCameraStatus.recording_filename || 'camera-recording.mp4')">
                    <Download :size="14" />
                    <span>下载录像</span>
                  </button>
                </div>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                <Upload class="text-emerald-400" :size="16"/>
                已上传媒体 ({{ store.mediaItems.length }})
              </h3>
              <div v-if="store.mediaItems.length" class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                <article v-for="media in store.mediaItems" :key="media.id" class="glass-panel p-3 flex gap-3 items-center min-w-0">
                  <div class="w-20 h-16 shrink-0 rounded-lg overflow-hidden bg-zinc-900 border border-white/[0.06] flex items-center justify-center">
                    <img v-if="media.thumbnail_url || media.object_url" :src="normalizeAssetUrl(media.thumbnail_url || media.object_url)" class="w-full h-full object-cover" alt="" />
                    <Film v-else-if="media.media_type === 'video' || media.media_type === 'camera'" :size="18" class="text-zinc-500" />
                    <FileImage v-else :size="18" class="text-zinc-500" />
                  </div>
                  <div class="min-w-0 flex-1">
                    <h4 class="text-xs font-semibold text-white truncate">{{ media.filename || media.original_filename || `媒体 #${media.id}` }}</h4>
                    <p class="mt-1 text-[10px] text-zinc-500 truncate">{{ media.media_type || '-' }} · {{ media.status || '-' }} · 目标 {{ media.target_count ?? 0 }}</p>
                    <p class="mt-1 text-[10px] text-zinc-600 truncate">{{ media.created_at || '-' }}</p>
                  </div>
                </article>
              </div>
              <div v-else class="empty-panel h-28 border border-dashed border-white/[0.06] rounded-xl bg-zinc-900/20">
                <Upload :size="20" class="text-zinc-600" />
                <span class="text-[11px] text-zinc-500">暂无上传媒体</span>
              </div>
            </div>

        </div>
        </template>

        <CoreRealtimeView v-else-if="activeView === 'realtime'" />
        <template v-else-if="activeView === 'results'">
          <div v-if="store.activeIsCameraProject" class="shrink-0 border-b border-white/10 p-4">
            <h3 class="mb-2 text-sm font-semibold">实时相机预览</h3>
            <div class="relative mx-auto aspect-video max-h-64 overflow-hidden rounded-xl bg-zinc-950">
              <LiveCameraPreview :key="coreCameraPreviewKey" :src="coreCameraFrameUrl" :stream-endpoint="`/api/v1/projects/${store.coreProjectId}/camera/preview-stream`" alt="摄像头实时预览" />
              <span v-if="!coreMonitorOnline" class="absolute inset-0 flex items-center justify-center text-xs text-zinc-500">等待相机画面</span>
            </div>
          </div>
        <CoreSearchResultsView
          :fallback-query-text="searchForm.text"
          :fallback-query-image-url="searchImagePreviewUrl"
          :fallback-query-kind="lastSearchKind"
          @preview-frame="previewResultFrame"
          @preview-media="previewResultMedia"
          @download="downloadSearchResult"
        />
        </template>
        <CoreHistoryView v-else-if="activeView === 'history'" @close="activeView = 'workspace'" @open-results="activeView = 'results'" />
        <CoreAuditView v-else-if="activeView === 'audit'" @close="activeView = 'workspace'" />
      </section>
    </div>

    </div>

    <MediaPreviewDialog :preview="mediaPreview" variant="dark" @close="mediaPreview.open = false" />
    <CoreAccountDialog :open="showLoginModal" @close="showLoginModal = false" />
    <CoreProjectDialog :open="showProjectModal" @close="showProjectModal = false" />
  </main>
</template>
