<script setup lang="ts">
import { onUnmounted, reactive, ref } from "vue";
import { Camera, Eye, Film, Map, Play, RefreshCw, Square, Upload } from "@lucide/vue";

import LiveCameraPreview from "../LiveCameraPreview.vue";
import { normalizeAssetUrl } from "../../lib/api/http";
import { useWorkbenchStore } from "../../stores/workbench";
import { clampProgress, progressPercent, useScenicLibraryStatus } from "./useScenicLibraryStatus";


type MonitorInfoTab = "library" | "jobs" | "media";

const emit = defineEmits<{
  (event: "preview", payload: {
    title: string;
    source?: string;
    mediaType?: string;
    downloadSource?: string;
    downloadName?: string;
  }): void;
}>();

const store = useWorkbenchStore();
const monitorInfoTab = ref<MonitorInfoTab>("library");
const simulationPreviewUrl = ref("");
const selectedSimulationPointId = ref(0);
const cameraPointForm = reactive({ name: "园区点位", cameraSource: "0", role: "play", location: "" });
const {
  runningCameraCount,
  ingestingCameraCount,
  activeCameraPointCount,
  targetCount,
  indexedMediaCount,
  libraryProgress,
  libraryStatusText,
  libraryDetailText,
} = useScenicLibraryStatus();

onUnmounted(() => {
  if (simulationPreviewUrl.value) URL.revokeObjectURL(simulationPreviewUrl.value);
});

function jobLabel(status?: string): string {
  if (status === "queued") return "排队";
  if (status === "running") return "处理中";
  if (status === "succeeded") return "完成";
  if (status === "failed") return "失败";
  return status || "未知";
}

function jobProgress(job: { progress?: number }): number {
  return clampProgress(Number(job.progress || 0));
}

function jobMessage(job: { message?: string; error?: string | null }): string {
  return job.error || job.message || "等待处理";
}

function simulationPointId(): number {
  return selectedSimulationPointId.value || store.scenicCameraWall[0]?.point_id || store.scenicCameraPoints[0]?.id || 0;
}

async function uploadSimulationVideo(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] || null;
  if (!file) return;
  if (simulationPreviewUrl.value) URL.revokeObjectURL(simulationPreviewUrl.value);
  simulationPreviewUrl.value = URL.createObjectURL(file);
  await store.uploadScenicSimulationVideo(simulationPointId(), file);
  input.value = "";
}

async function toggleScenicDevice(source: string, label: string, event: Event): Promise<void> {
  await store.setScenicCameraEnabled(source, label, (event.target as HTMLInputElement).checked);
}

function scenicDeviceEnabled(source: string): boolean {
  const point = store.scenicCameraPoints.find((item) => item.camera_source === source || item.camera_id === source);
  return point?.is_active !== false;
}
</script>

<template>
  <section class="monitor-layout">
    <div class="monitor-main">
      <div class="toolbar-row">
        <button class="scenic-button subtle" type="button" @click="store.syncScenicCameraPoints()"><RefreshCw :size="15" /><span>同步本机相机</span></button>
        <button class="scenic-button primary" type="button" :disabled="ingestingCameraCount > 0 || activeCameraPointCount === 0" @click="store.toggleScenicCameraWall(true)"><Play :size="14" /><span>启动监控墙</span></button>
        <button class="scenic-button subtle" type="button" :disabled="ingestingCameraCount === 0" @click="store.toggleScenicCameraWall(false)"><Square :size="13" /><span>停止监控墙</span></button>
        <span class="toolbar-note">在线 {{ runningCameraCount }} · 已入库目标 {{ targetCount }}</span>
      </div>

      <div v-if="store.scenicCameraWall.length" class="camera-grid">
        <article v-for="camera in store.scenicCameraWall" :key="camera.point_id || camera.camera_source" class="camera-card">
          <div class="camera-frame">
            <LiveCameraPreview v-if="camera.latest_frame_url" :src="normalizeAssetUrl(camera.latest_frame_url) + '?v=' + (camera.latest_frame_version || 0)" :stream-endpoint="camera.point_id ? `/api/v1/scenic/projects/${store.scenicProjectId}/camera-points/${camera.point_id}/preview-stream` : undefined" auth-scope="scenic" alt="摄像头实时画面" />
            <span v-else>{{ camera.running ? "等待画面" : "未启动" }}</span>
          </div>
          <div class="camera-meta">
            <div><strong>{{ camera.name || "摄像头" }}</strong><small>{{ camera.location || camera.role || "园区点位" }} · {{ camera.camera_source || "0" }}</small></div>
            <span class="capture-pill" :class="{ on: camera.running }">{{ camera.running ? "采集" : "离线" }}</span>
          </div>
          <p>目标 {{ camera.indexed_targets || 0 }} · {{ camera.message || "-" }}</p>
          <p v-if="camera.preview_width">{{ camera.preview_width }}×{{ camera.preview_height }} · 采集 {{ camera.capture_fps || 0 }} FPS · 预览源 {{ camera.preview_fps || 0 }} FPS</p>
          <div class="mini-actions">
            <button class="scenic-button compact subtle" type="button" :disabled="!camera.recording_url" @click="emit('preview', { title: camera.recording_filename || camera.name || '相机录像', source: camera.recording_url, mediaType: 'video', downloadSource: camera.recording_url, downloadName: camera.recording_filename || 'camera_recording.mp4' })"><Eye :size="13" /><span>录像</span></button>
            <label class="scenic-button compact subtle file-button"><Upload :size="13" /><span>导入录像</span><input type="file" accept="video/*" aria-label="选择待导入录像" @change="uploadSimulationVideo" @click="selectedSimulationPointId = camera.point_id || 0" /></label>
          </div>
        </article>
      </div>
      <div v-else class="empty-state large"><Camera :size="28" /><h3>暂无摄像头点位</h3><p>同步本机相机或手动添加点位后，可启动景区监控墙。</p></div>

      <article class="panel monitor-info">
        <div class="tab-strip">
          <button :class="{ active: monitorInfoTab === 'library' }" type="button" @click="monitorInfoTab = 'library'">库状态</button>
          <button :class="{ active: monitorInfoTab === 'jobs' }" type="button" @click="monitorInfoTab = 'jobs'">处理任务</button>
          <button :class="{ active: monitorInfoTab === 'media' }" type="button" @click="monitorInfoTab = 'media'">媒体目标</button>
        </div>
        <div v-if="monitorInfoTab === 'library'" class="metric-row">
          <div class="metric-tile"><span>媒体</span><strong>{{ store.scenicProjectStatus?.media_total ?? store.scenicMediaItems.length }}</strong></div>
          <div class="metric-tile"><span>目标</span><strong>{{ targetCount }}</strong></div>
          <div class="metric-tile"><span>已入库</span><strong>{{ indexedMediaCount }}</strong></div>
          <p>{{ libraryStatusText }} · {{ libraryDetailText }}</p>
        </div>
        <div v-else-if="monitorInfoTab === 'jobs'" class="compact-list">
          <div v-for="job in store.jobs" :key="job.id" class="list-row"><strong>{{ (job.type || job.job_type) === "media_index" ? "目标检测/特征提取" : (job.type || job.job_type || "任务") }} · {{ jobLabel(job.status) }}</strong><div class="progress-track"><div :style="{ width: progressPercent(jobProgress(job)) }"></div></div><span>{{ progressPercent(jobProgress(job)) }}</span></div>
          <div v-if="!store.jobs.length" class="empty-inline">暂无后台任务</div>
        </div>
        <div v-else class="compact-list">
          <div v-for="media in store.scenicMediaItems" :key="media.id" class="list-row"><strong>{{ media.filename || "媒体" }}</strong><span>{{ media.status || "-" }} · 目标 {{ media.target_count || 0 }}</span></div>
          <div v-if="!store.scenicMediaItems.length" class="empty-inline">暂无媒体入库</div>
        </div>
      </article>
    </div>

    <aside class="panel simulator-panel">
      <div class="panel-head"><h3>录像导入</h3><Film :size="18" /></div>
      <p class="panel-copy">选择摄像头点位并导入录像，处理完成后即可用于游客画面匹配。</p>
      <select v-model.number="selectedSimulationPointId" class="scenic-field" aria-label="目标摄像头"><option :value="0">选择目标摄像头</option><option v-for="point in store.scenicCameraWall" :key="point.point_id || point.camera_source" :value="point.point_id || 0">{{ point.name || point.camera_source }}</option></select>
      <label class="scenic-button primary full-width file-button"><Upload :size="15" /><span>选择并导入录像</span><input type="file" accept="video/*" aria-label="选择并导入录像" @change="uploadSimulationVideo" /></label>
      <div class="library-mini"><div><strong>入库状态</strong><span>{{ progressPercent(libraryProgress) }}</span></div><div class="progress-track"><div :style="{ width: progressPercent(libraryProgress) }"></div></div><p>{{ libraryDetailText }}</p></div>
      <div class="sim-preview"><video v-if="simulationPreviewUrl" :src="simulationPreviewUrl" controls muted></video><span v-else>视频预览</span></div>
      <div class="compact-list"><div v-for="job in store.jobs.slice(0, 3)" :key="job.id" class="list-row"><strong>{{ (job.type || job.job_type) === "media_index" ? "目标检测/特征" : (job.type || job.job_type || "任务") }} · {{ jobLabel(job.status) }}</strong><span>{{ progressPercent(jobProgress(job)) }}</span><small>{{ jobMessage(job) }}</small></div></div>
    </aside>

    <article class="panel device-panel">
      <div class="panel-head"><h3>可用摄像头</h3><button class="scenic-button compact subtle" type="button" @click="store.refreshSystem()">刷新设备</button></div>
      <div v-if="store.scenicCameraDevices.length" class="device-grid">
        <label v-for="device in store.scenicCameraDevices" :key="String(device.source)" class="device-row"><span><strong>{{ device.label || device.name || `摄像头 ${device.source}` }}</strong><small>ID {{ device.source }} · {{ device.width || "-" }}x{{ device.height || "-" }}</small></span><input type="checkbox" :checked="scenicDeviceEnabled(String(device.source))" @change="toggleScenicDevice(String(device.source), device.label || device.name || `摄像头 ${device.source}`, $event)" /></label>
      </div>
      <div class="manual-point">
        <input v-model="cameraPointForm.name" class="scenic-field" placeholder="点位名称" aria-label="点位名称" />
        <input v-model="cameraPointForm.cameraSource" class="scenic-field" placeholder="设备 ID，如 0 或 entrance" aria-label="摄像头来源" />
        <select v-model="cameraPointForm.role" class="scenic-field" aria-label="点位角色"><option value="entry">入口</option><option value="play">游玩</option><option value="exit">出口</option></select>
        <button class="scenic-button primary" type="button" @click="store.createScenicCameraPoint(cameraPointForm.name, cameraPointForm.cameraSource, cameraPointForm.role, cameraPointForm.location)"><Map :size="15" /><span>新增点位</span></button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.monitor-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; }
.monitor-main { display: grid; min-width: 0; gap: 14px; }
.panel,
.camera-card,
.list-row,
.device-row,
.library-mini,
.empty-state { border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); color: #e4e4e7; }
.panel { padding: 16px; }
.toolbar-row,
.panel-head,
.camera-meta,
.mini-actions,
.library-mini > div,
.list-row,
.device-row { display: flex; align-items: center; gap: 10px; }
.toolbar-row { flex-wrap: wrap; }
.toolbar-note { margin-left: auto; color: #a1a1aa; font-size: 12px; }
.panel-head,
.camera-meta,
.library-mini > div,
.device-row { justify-content: space-between; }
.panel-head { margin-bottom: 12px; }
.panel-head h3,
.panel p,
.empty-state h3,
.empty-state p { margin: 0; }
.camera-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.camera-card { padding: 10px; }
.camera-frame,
.sim-preview { display: grid; min-height: 190px; place-items: center; overflow: hidden; border-radius: 8px; background: rgb(0 0 0 / 0.32); color: #71717a; }
.camera-frame { position: relative; aspect-ratio: 16 / 9; }
.camera-frame :deep(.live-camera-preview img),
.sim-preview video { width: 100%; height: 100%; object-fit: contain; }
.camera-meta { position: static; margin-top: 9px; }
.camera-meta strong,
.camera-meta small { display: block; }
.camera-meta small,
.camera-card p,
.panel-copy,
.list-row span,
.list-row small,
.library-mini p { color: #a1a1aa; font-size: 11px; }
.capture-pill { border-radius: 999px; padding: 4px 7px; background: rgb(159 18 57 / 0.22); color: #fecdd3; font-size: 11px; }
.capture-pill.on { background: rgb(22 163 74 / 0.18); color: #bbf7d0; }
.file-button input { display: none; }
.monitor-info { min-width: 0; }
.tab-strip { display: flex; gap: 5px; border-bottom: 1px solid rgb(255 255 255 / 0.08); margin-bottom: 12px; }
.tab-strip button { border: 0; border-bottom: 2px solid transparent; background: transparent; padding: 8px 10px; color: #a1a1aa; }
.tab-strip button.active { border-color: #5eead4; color: #fff; }
.metric-row { display: grid; grid-template-columns: repeat(3, 120px) minmax(0, 1fr); gap: 10px; align-items: center; }
.metric-tile { display: grid; gap: 4px; border-radius: 8px; background: rgb(255 255 255 / 0.04); padding: 10px; }
.metric-tile span { color: #a1a1aa; font-size: 11px; }
.metric-tile strong { color: #93c5fd; font-size: 20px; }
.compact-list { display: grid; gap: 7px; }
.list-row { min-height: 42px; padding: 8px 10px; }
.list-row strong { min-width: 0; flex: 1; }
.progress-track { width: 120px; height: 6px; overflow: hidden; border-radius: 999px; background: rgb(255 255 255 / 0.09); }
.progress-track > div { height: 100%; background: linear-gradient(90deg, #14b8a6, #6366f1); }
.simulator-panel { min-width: 0; display: grid; align-content: start; gap: 12px; }
.library-mini { display: grid; gap: 8px; padding: 10px; }
.library-mini p { margin: 0; }
.sim-preview { min-height: 180px; }
.device-panel { grid-column: 1 / -1; }
.device-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.device-row { min-height: 54px; padding: 9px 10px; }
.device-row strong,
.device-row small { display: block; }
.device-row small { margin-top: 3px; color: #a1a1aa; font-size: 11px; }
.device-row input { accent-color: #14b8a6; }
.manual-point { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 130px 130px; gap: 8px; }
.scenic-field { width: 100%; min-height: 38px; border: 1px solid rgb(255 255 255 / 0.12); border-radius: 6px; background: rgb(0 0 0 / 0.24); padding: 8px 10px; color: #fff; }
.scenic-button { display: inline-flex; min-height: 36px; align-items: center; justify-content: center; gap: 7px; border: 1px solid rgb(255 255 255 / 0.12); border-radius: 8px; background: transparent; padding: 8px 12px; color: #e4e4e7; font-size: 13px; font-weight: 800; }
.scenic-button.primary { border-color: rgb(45 212 191 / 0.42); background: linear-gradient(135deg, rgb(20 184 166 / 0.95), rgb(99 102 241 / 0.82)); color: #fff; }
.scenic-button.subtle { background: rgb(255 255 255 / 0.06); }
.scenic-button.compact { min-height: 30px; padding: 5px 9px; font-size: 12px; }
.scenic-button.full-width { width: 100%; }
.scenic-button:disabled { opacity: 0.45; pointer-events: none; }
.empty-state { display: grid; min-height: 260px; place-items: center; gap: 8px; border-style: dashed; color: #a1a1aa; text-align: center; }
.empty-state.large { min-height: 390px; }
.empty-inline { display: grid; min-height: 80px; place-items: center; color: #71717a; }

@media (max-width: 1260px) {
  .monitor-layout { grid-template-columns: 1fr; }
  .camera-grid { grid-template-columns: 1fr; }
  .device-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

<style scoped>
.list-row small { min-width: 0; overflow-wrap: anywhere; }
.simulator-panel .list-row { flex-wrap: wrap; }
.simulator-panel .list-row small { flex-basis: 100%; }
</style>
