<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useWorkbenchStore } from "../../stores/workbench";
import { useRealtimeStore } from "../../stores/realtime";
import { realtimeApi, type Crop, type RealtimeInput, type RealtimeSource } from "../../lib/api/realtime";
import { subscribeAuthSession } from "../../lib/api/http";

const workbench = useWorkbenchStore();
const realtime = useRealtimeStore();
const reference = ref<RealtimeInput | null>(null);
const video = ref<RealtimeInput | null>(null);
const crop = ref<Crop | null>(null);
const cropPreview = ref("");
const sourceKind = ref("upload");
const mediaId = ref(0);
const mediaPreview = ref("");
const previewLoading = ref(false);
const previewError = ref("");
let previewVersion = 0;
const sourcePreview = computed(() => sourceKind.value === "upload" ? video.value?.preview : sourceKind.value === "media" ? mediaPreview.value : "");
const camera = ref("0");
const cameraDevices = ref<{ source: string; label: string }[]>([]);
const camerasLoading = ref(false);
const camerasError = ref("");
let cameraRequest = 0;
let cameraRefreshTimer: ReturnType<typeof setTimeout> | undefined;
const showOthers = ref(false);
const busy = ref(false);
const error = ref("");
const manual = ref(false);
let version = 0;
let anchor: [number, number] | null = null;
const videos = computed(() => workbench.mediaItems.filter(m => ["video", "camera"].includes(m.media_type || "")));
const active = computed(() => ["starting", "running", "paused", "stopping", "reconnecting"].includes(realtime.status));
const labels: Record<string, string> = { reconnecting: "正在重连", searching: "寻找中", pending: "待确认", confirmed: "已确认", lost: "暂时丢失", reconfirmed: "重新确认", idle: "尚未开始", starting: "正在准备模型", running: "识别中", paused: "已暂停", stopping: "正在停止", stopped: "已停止", completed: "视频已结束", failed: "识别失败" };
const target = computed(() => realtime.frame?.people.find(p => p.target));
const ready = computed(() => workbench.isAuthenticated && workbench.coreProjectId && reference.value && crop.value && (sourceKind.value === "upload" ? video.value : sourceKind.value === "media" ? mediaId.value : camera.value.trim()));

async function remove(input: RealtimeInput | null, project: number) {
  if (input) try { await realtimeApi.remove(project, input.id); } catch { /* Active/expired inputs are reclaimed by the server. */ }
}
async function upload(event: Event, kind: "image" | "video") {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const current = ++version, project = workbench.coreProjectId;
  busy.value = true; error.value = "";
  await realtime.stop();
  if (current !== version) return;
  const old = kind === "image" ? reference.value : video.value;
  if (kind === "image") { reference.value = null; crop.value = null; } else video.value = null;
  await remove(old, project);
  if (current !== version) return;
  try {
    const result = await realtimeApi.upload(project, file, kind);
    if (current !== version) { await remove(result, project); return; }
    if (kind === "image") {
      reference.value = result;
      manual.value = !result.boxes?.length;
      crop.value = result.boxes?.length === 1 ? result.boxes[0].box : null;
    } else video.value = result;
  } catch (reason) { if (current === version) error.value = String(reason); }
  finally { if (current === version) busy.value = false; }
}
function point(event: PointerEvent): [number, number] {
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
}
function beginCrop(event: PointerEvent) {
  if (!manual.value || active.value) return;
  anchor = point(event); (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function endCrop(event: PointerEvent) {
  if (!anchor) return;
  const end = point(event);
  if (Math.abs(end[0]-anchor[0]) > .01 && Math.abs(end[1]-anchor[1]) > .01)
    crop.value = [Math.min(anchor[0],end[0]), Math.min(anchor[1],end[1]), Math.max(anchor[0],end[0]), Math.max(anchor[1],end[1])];
  anchor = null;
}
function boxStyle(box: Crop) { return { left: `${box[0]*100}%`, top: `${box[1]*100}%`, width: `${(box[2]-box[0])*100}%`, height: `${(box[3]-box[1])*100}%` }; }
watch([crop, reference], async () => {
  cropPreview.value = "";
  const selected = crop.value, source = reference.value;
  if (!selected || !source?.preview) return;
  const image = new Image(); image.src = source.preview;
  await image.decode();
  if (selected !== crop.value || source !== reference.value) return;
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round((selected[2]-selected[0])*image.width));
  canvas.height = Math.max(1, Math.round((selected[3]-selected[1])*image.height));
  canvas.getContext("2d")?.drawImage(image, selected[0]*image.width, selected[1]*image.height, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
  cropPreview.value = canvas.toDataURL("image/jpeg");
});
async function start() {
  if (!reference.value || !crop.value) return;
  const source: RealtimeSource = sourceKind.value === "upload" ? { input_id: video.value!.id } : sourceKind.value === "media" ? { media_id: mediaId.value } : { camera: camera.value };
  await realtime.start(workbench.coreProjectId, reference.value.id, crop.value, source, showOthers.value);
}
function reset(project: number) {
  ++version; ++previewVersion; busy.value = false;
  mediaId.value = 0; mediaPreview.value = ""; previewLoading.value = false; previewError.value = "";
  void realtime.stop();
  void remove(reference.value, project); void remove(video.value, project);
  reference.value = video.value = null; crop.value = null;
}
watch(() => workbench.coreProjectId, (_, previous) => reset(previous));
async function loadCameras() {
  const project = workbench.coreProjectId;
  const request = ++cameraRequest;
  clearTimeout(cameraRefreshTimer);
  if (!project || !workbench.isAuthenticated) return;
  camerasLoading.value = true;
  camerasError.value = "";
  try {
    const response = await realtimeApi.cameras(project);
    if (request !== cameraRequest) return;
    cameraDevices.value = response.devices || [];
  } catch {
    if (request === cameraRequest) camerasError.value = "摄像头列表加载失败，请刷新重试";
  } finally {
    if (request === cameraRequest) {
      camerasLoading.value = false;
      if (sourceKind.value === "camera") cameraRefreshTimer = setTimeout(() => void loadCameras(), 5000);
    }
  }
}
watch(() => [workbench.coreProjectId, workbench.isAuthenticated], () => {
  cameraDevices.value = [];
  camera.value = workbench.activeProject?.camera_source || "0";
  void loadCameras();
}, { immediate: true });
watch(sourceKind, kind => {
  clearTimeout(cameraRefreshTimer);
  if (kind === "camera") void loadCameras();
});
watch([sourceKind, mediaId, camera], () => { if (active.value) void realtime.stop(); });
watch([sourceKind, mediaId, () => workbench.coreProjectId, () => workbench.isAuthenticated], async () => {
  const current = ++previewVersion;
  mediaPreview.value = ""; previewError.value = ""; previewLoading.value = false;
  if (sourceKind.value !== "media" || !mediaId.value || !workbench.coreProjectId || !workbench.isAuthenticated) return;
  previewLoading.value = true;
  try {
    const result = await realtimeApi.preview(workbench.coreProjectId, mediaId.value);
    if (current === previewVersion) mediaPreview.value = result.preview;
  } catch { if (current === previewVersion) previewError.value = "视频预览加载失败，可重新选择视频重试"; }
  finally { if (current === previewVersion) previewLoading.value = false; }
});
// Session-owned uploads are deleted on completion; require fresh inputs for a new run.
watch(() => realtime.status, value => { if (realtime.sessionId && ["completed", "failed", "stopped"].includes(value)) { reference.value = video.value = null; crop.value = null; } });
const unsubscribe = subscribeAuthSession(change => { if (change.scope === "core" && change.reason !== "refresh") reset(workbench.coreProjectId); });
onUnmounted(() => { ++cameraRequest; clearTimeout(cameraRefreshTimer); unsubscribe(); reset(workbench.coreProjectId); });
</script>

<template>
  <section class="realtime-view">
    <header><div><p class="eyebrow">LIVE REID · 单路单目标</p><h1>实时行人识别</h1></div><span>相机默认 640 · 目标 30 FPS / 视频采样 10 FPS</span></header>
    <div v-if="!workbench.isAuthenticated || !workbench.coreProjectId" class="notice">请先登录并选择项目。</div>
    <div class="realtime-layout">
      <aside class="controls">
        <fieldset :disabled="active || busy || !workbench.coreProjectId || !workbench.isAuthenticated">
          <legend>1 · 选择目标人物</legend>
          <input aria-label="上传目标照片" type="file" accept="image/*" @change="upload($event, 'image')" />
          <div v-if="reference?.preview" class="reference" @pointerdown="beginCrop" @pointerup="endCrop" @pointercancel="anchor = null">
            <img :src="reference.preview" alt="目标照片，点击人物框选择或拖动手动裁剪" draggable="false" />
            <button v-for="(box, index) in reference.boxes" v-show="!manual" :key="index" class="person-box" :style="boxStyle(box.box)" :aria-label="`选择人物 ${index + 1}`" @click="crop = box.box">{{ index + 1 }}</button>
            <div v-if="crop" class="selected-box" :style="boxStyle(crop)"></div>
          </div>
          <label v-if="reference"><input v-model="manual" type="checkbox" /> 手动拖动裁剪</label>
          <p v-if="reference && !crop">{{ manual ? '在照片上拖出目标区域。' : '点击照片中的一个人物框。' }}</p>
          <img v-if="cropPreview" class="crop-preview" :src="cropPreview" alt="选定目标裁剪预览" />
          <legend>2 · 选择视频来源</legend>
          <select v-model="sourceKind" aria-label="视频来源"><option value="upload">临时上传视频</option><option value="media">项目已有视频</option><option value="camera">USB / RTSP 摄像头</option></select>
          <input v-if="sourceKind === 'upload'" aria-label="上传识别视频" type="file" accept="video/*" @change="upload($event, 'video')" />
          <p v-if="video && sourceKind === 'upload'">视频已准备，仅用于本次识别</p>
          <select v-if="sourceKind === 'media'" v-model.number="mediaId" aria-label="项目视频"><option :value="0">选择项目视频</option><option v-for="media in videos" :key="media.id" :value="media.id">{{ media.filename || media.original_filename || media.id }}</option></select>
          <template v-if="sourceKind === 'camera'">
            <select v-model="camera" aria-label="已配置摄像头"><option v-if="!cameraDevices.some(device => device.source === camera)" :value="camera">{{ camera }}</option><option v-for="device in cameraDevices" :key="device.source" :value="device.source">{{ device.label }} · {{ device.source }}</option></select>
            <button type="button" :disabled="camerasLoading" @click="loadCameras">{{ camerasLoading ? "正在加载摄像头…" : "刷新摄像头列表" }}</button>
            <input v-model="camera" list="realtime-cameras" aria-label="摄像头地址" placeholder="设备 ID，如 0 或 entrance" />
            <datalist id="realtime-cameras"><option v-for="device in cameraDevices" :key="String(device.source)" :value="String(device.source)">{{ device.label }}</option></datalist>
            <p>请选择连接到服务器的 USB 摄像头、已启用的虚拟相机或网络摄像头，也可填写管理员配置的设备 ID。此页面仅用于实时识别，不录制或保存视频。</p>
            <p v-if="camerasError" role="alert">{{ camerasError }}</p>
          </template>
          <label><input v-model="showOthers" type="checkbox" /> 显示其他行人的灰框</label>
        </fieldset>
        <div class="actions">
          <button class="primary-button" :disabled="!ready || active || busy" @click="start">开始识别</button>
          <button v-if="realtime.status === 'running'" class="ghost-button" @click="realtime.control('pause')">暂停</button>
          <button v-if="realtime.status === 'paused'" class="ghost-button" @click="realtime.control('resume')">继续</button>
          <button v-if="active" class="ghost-button" @click="reset(workbench.coreProjectId)">停止</button>
        </div>
        <p v-if="busy" role="status">正在上传并准备素材…</p>
        <p v-if="error || realtime.error" class="error" role="alert">{{ error || realtime.error }}</p>
      </aside>
      <div class="display">
        <div class="screen"><img v-if="realtime.image" :src="realtime.image" alt="实时行人识别画面" /><img v-else-if="sourcePreview && !active" :src="sourcePreview" alt="所选视频首帧预览" /><div v-else class="empty">{{ previewLoading ? '正在加载视频预览…' : previewError || labels[realtime.status] || '等待画面' }}<small>选择目标人物和来源后开始识别</small></div></div>
        <div class="metrics" role="status">
          <div><span>目标状态</span><strong>{{ realtime.status === 'paused' ? '已暂停' : labels[realtime.frame?.target_state || realtime.status] }}</strong></div>
          <div><span>视频位置 / 相机时间</span><strong>{{ realtime.frame ? (sourceKind === 'camera' ? new Date(realtime.frame.source_time * 1000).toLocaleTimeString() : `${realtime.frame.source_time.toFixed(1)} s`) : '—' }}</strong></div>
          <div><span>处理 FPS</span><strong>{{ realtime.frame?.fps ?? '—' }}</strong></div>
          <div><span>估计端到端延迟</span><strong>{{ realtime.frame ? `${Math.round(realtime.latency)} ms` : '—' }}</strong></div>
          <div><span>ReID 相似度</span><strong>{{ target?.similarity.toFixed(3) ?? '—' }}</strong></div>
          <div><span>检测置信度</span><strong>{{ target?.detection_confidence.toFixed(3) ?? '—' }}</strong></div>
        </div>
        <p class="hint">绿色：已确认 · 黄色：待确认 · 相似度不是身份概率。帧率取决于设备与画面人数；跨机器延迟估计需要时钟同步。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.realtime-view{height:100%;overflow:auto;padding:28px;color:#e4e4e7;background:#0a0b0c}header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px}h1{font-size:22px;font-weight:700}header span,.hint,small,p{font-size:12px;color:#a1a1aa}.realtime-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px}.controls{background:#141518;border:1px solid #27272a;border-radius:16px;padding:20px}fieldset{display:grid;gap:14px;min-width:0}fieldset:disabled{opacity:.65}legend{font-size:14px;font-weight:600;margin:12px 0}select,input:not([type=checkbox]){width:100%;min-width:0;font-size:12px;padding:10px;border:1px solid #3f3f46;border-radius:8px;background:#18181b;color:#e4e4e7}label{font-size:12px;display:flex;align-items:center;gap:8px}.reference{position:relative;touch-action:none;user-select:none}.reference img{display:block;width:100%;border-radius:8px}.person-box,.selected-box{position:absolute;border:2px solid #facc15;background:transparent;color:white}.person-box{cursor:pointer}.selected-box{pointer-events:none;border-color:#34d399}.crop-preview{max-height:120px;max-width:100%;object-fit:contain;justify-self:start}.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:20px}.actions button{font-size:12px}.screen{aspect-ratio:16/9;background:#020304;border:1px solid #27272a;border-radius:16px;overflow:hidden;display:flex;align-items:center;justify-content:center}.screen img{width:100%;height:100%;object-fit:contain}.empty{text-align:center;color:#a1a1aa}.empty small{display:block;margin-top:12px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:20px 0}.metrics div{padding:16px;background:#141518;border-radius:12px}.metrics span{display:block;font-size:11px;color:#a1a1aa}.metrics strong{display:block;margin-top:6px;font-size:16px}.error{color:#fda4af;margin-top:12px}.notice{padding:12px;border:1px solid #a16207;border-radius:8px;margin-bottom:16px}@media(max-width:900px){.realtime-layout{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}header{align-items:start;flex-direction:column}}
</style>
