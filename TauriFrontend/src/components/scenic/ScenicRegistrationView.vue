<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import { Camera, ImageUp, UserPlus, Users, X } from "@lucide/vue";

import { normalizeAssetUrl } from "../../lib/api/http";
import type { ScenicCameraWallItem, ScenicSnapshot } from "../../lib/api/scenic";
import ScenicTouristFilters from "./ScenicTouristFilters.vue";
import LiveCameraPreview from "../LiveCameraPreview.vue";
import { useWorkbenchStore } from "../../stores/workbench";
import { scenicStatusClass, scenicStatusLabel } from "./viewHelpers";


const emit = defineEmits<{
  (event: "select-tourist", touristId: number): void;
}>();

const store = useWorkbenchStore();
const photoSource = ref<"camera" | "upload">("camera");
const touristFile = ref<File | null>(null);
const touristSnapshot = ref<ScenicSnapshot | null>(null);
const touristFilePreviewUrl = ref("");
const touristForm = reactive({
  ticketCode: "",
  visitorLabel: "",
  contactHint: "",
  visitorType: "普通游客",
  collectContact: true,
  attention: false,
  consentGranted: false,
});
const selectedEntryPoint = ref(0);
const capturing = ref(false);
function cameraPointId(camera: ScenicCameraWallItem): number {
  return camera.id ?? camera.point_id ?? 0;
}
const captureCameras = computed(() => store.scenicCameraWall.filter((item) => cameraPointId(item) > 0 && item.is_active !== false));
const selectedCamera = computed(() => captureCameras.value.find((item) => cameraPointId(item) === selectedEntryPoint.value));
watch(captureCameras, (items) => {
  if (!items.some((item) => cameraPointId(item) === selectedEntryPoint.value)) {
    const preferred = items.find((item) => item.role === "entry" && (item.monitor_running || item.running))
      || items.find((item) => item.monitor_running || item.running)
      || items.find((item) => item.role === "entry")
      || items[0];
    selectedEntryPoint.value = preferred ? cameraPointId(preferred) : 0;
  }
}, { immediate: true });
watch(() => [store.scenicProjectId, selectedEntryPoint.value] as const, ([projectId, pointId]) => {
  if (projectId && pointId) void store.prepareScenicCameraPreview(pointId);
  else store.releaseScenicCameraPreview();
}, { immediate: true });

const selectedCameraOnline = computed(() => Boolean(selectedCamera.value?.monitor_running || selectedCamera.value?.running));
const cameraPreviewUrl = computed(() => {
  const source = normalizeAssetUrl(selectedCamera.value?.latest_frame_url || "");
  if (!source) return "";
  const url = new URL(source, window.location.href);
  url.searchParams.set("v", String(selectedCamera.value?.latest_frame_version || 0));
  return url.href;
});

onUnmounted(() => {
  store.releaseScenicCameraPreview();
  revokeTouristPreview();
});

function revokeTouristPreview(): void {
  if (touristFilePreviewUrl.value) URL.revokeObjectURL(touristFilePreviewUrl.value);
  touristFilePreviewUrl.value = "";
}

function touristEntryPreviewUrl(): string {
  if (touristSnapshot.value?.snapshot_url) return normalizeAssetUrl(touristSnapshot.value.snapshot_url);
  return touristFilePreviewUrl.value;
}

function clearTouristEntry(): void {
  touristFile.value = null;
  touristSnapshot.value = null;
  revokeTouristPreview();
}

function selectTouristFile(event: Event): void {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] || null;
  if (!file) return;
  touristFile.value = file;
  photoSource.value = "upload";
  touristSnapshot.value = null;
  revokeTouristPreview();
  touristFilePreviewUrl.value = URL.createObjectURL(file);
  input.value = "";
}

async function captureTouristSnapshot(): Promise<void> {
  if (!selectedEntryPoint.value || !selectedCameraOnline.value || capturing.value) return;
  capturing.value = true;
  try {
    const snapshot = await store.captureScenicSnapshot(selectedEntryPoint.value);
    if (!snapshot) return;
    clearTouristEntry();
    touristSnapshot.value = snapshot;
  } finally {
    capturing.value = false;
  }
}

async function submitTouristRegistration(): Promise<void> {
  const label = [
    touristForm.visitorLabel,
    touristForm.visitorType !== "普通游客" ? touristForm.visitorType : "",
    touristForm.attention ? "重点照看" : "",
  ].filter(Boolean).join(" · ");

  await store.registerTourist(
    touristFile.value || touristSnapshot.value,
    touristForm.ticketCode,
    label,
    touristForm.collectContact ? touristForm.contactHint : "",
    touristForm.consentGranted,
  );
  if (store.error) return;
  clearTouristEntry();
  touristForm.ticketCode = "";
  touristForm.visitorLabel = "";
  touristForm.contactHint = "";
  touristForm.consentGranted = false;
}

</script>

<template>
  <section class="registration-layout">
    <ol class="registration-steps" aria-label="登记步骤"><li class="active">1 采集照片</li><li>2 填写登记信息</li><li>3 完成登记</li></ol>
    <div class="panel photo-source-panel">
      <div class="panel-head"><div><h3>游客照片</h3><p class="surface-caption">上传照片或从园内摄像头抓拍</p></div></div>
      <div class="source-tabs" role="group" aria-label="照片来源">
        <button type="button" :aria-pressed="photoSource === 'camera'" :class="{ active: photoSource === 'camera' }" @click="photoSource = 'camera'">相机抓拍</button>
        <button type="button" :aria-pressed="photoSource === 'upload'" :class="{ active: photoSource === 'upload' }" @click="photoSource = 'upload'">上传照片</button>
      </div>
    <article v-show="photoSource === 'upload'" class="photo-panel">
      <div class="panel-head">
        <h3>上传照片</h3>
        <ImageUp :size="18" />
      </div>
      <div class="preview-stage">
        <label class="entry-drop">
          <img v-if="touristEntryPreviewUrl()" :src="touristEntryPreviewUrl()" alt="待登记游客照片" />
          <span v-else>选择入园照片</span>
          <input type="file" accept="image/*" aria-label="选择入园照片" @change="selectTouristFile" />
        </label>
      </div>
      <div class="button-row">
        <label class="scenic-button primary file-button">
          <ImageUp :size="15" />
          <span>选择照片</span>
          <input type="file" accept="image/*" aria-label="选择游客照片" @change="selectTouristFile" />
        </label>
        <button class="scenic-button subtle" type="button" :disabled="!touristFile && !touristSnapshot" @click="clearTouristEntry">
          <X :size="15" />
          <span>清除</span>
        </button>
      </div>
    </article>

    <article v-show="photoSource === 'camera'" class="photo-panel">
      <div class="panel-head">
        <h3>实时相机抓拍</h3>
        <span class="text-status" :class="selectedCameraOnline ? 'green' : 'red'">{{ selectedCameraOnline ? "在线" : "离线" }}</span>
      </div>
      <select v-model.number="selectedEntryPoint" class="scenic-field" aria-label="入口抓拍相机" :disabled="!captureCameras.length || capturing">
        <option v-if="!captureCameras.length" :value="0">暂无可用相机，请在园内摄像头中配置</option>
        <option v-for="camera in captureCameras" :key="cameraPointId(camera)" :value="cameraPointId(camera)">{{ camera.name || `相机 ${cameraPointId(camera)}` }}</option>
      </select>
      <div class="preview-stage">
        <div class="camera-preview">
          <LiveCameraPreview v-if="cameraPreviewUrl && photoSource === 'camera'" :key="selectedEntryPoint" :src="cameraPreviewUrl" :stream-endpoint="`/api/v1/scenic/projects/${store.scenicProjectId}/camera-points/${selectedEntryPoint}/preview-stream`" auth-scope="scenic" class="camera-live-stream" alt="实时相机画面" />
          <span v-else>{{ selectedCameraOnline ? "等待实时画面" : "监控未开启" }}</span>
        </div>
      </div>
      <div class="button-row">
        <button class="scenic-button primary" type="button" :disabled="!selectedEntryPoint || !selectedCameraOnline || !cameraPreviewUrl || capturing" @click="captureTouristSnapshot">
          <Camera :size="15" />
          <span>{{ capturing ? "抓拍中…" : "抓拍照片" }}</span>
        </button>
        <span v-if="touristSnapshot" class="surface-caption">已抓拍，可提交登记</span>
      </div>
    </article>
    </div>

    <article class="panel registration-form">
      <div class="panel-head">
        <h3>登记信息</h3>
        <UserPlus :size="18" />
      </div>
      <label class="form-label">门票号<input v-model="touristForm.ticketCode" class="scenic-field" placeholder="门票号" aria-label="门票号" /></label>
      <label class="form-label">游客备注<input v-model="touristForm.visitorLabel" class="scenic-field" placeholder="游客备注" aria-label="游客备注" /></label>
      <label class="form-label">联系方式<input v-model="touristForm.contactHint" class="scenic-field" :disabled="!touristForm.collectContact" placeholder="联系方式" aria-label="联系方式" /></label>
      <label class="form-label">游客类型<select v-model="touristForm.visitorType" class="scenic-field" aria-label="游客类型">
        <option>普通游客</option>
        <option>亲子家庭</option>
        <option>团体游客</option>
        <option>重点服务</option>
      </select></label>
      <label class="check-line"><input v-model="touristForm.collectContact" type="checkbox" />记录联系方式</label>
      <label class="check-line"><input v-model="touristForm.attention" type="checkbox" />重点照看</label>
      <label class="check-line"><input v-model="touristForm.consentGranted" type="checkbox" />已获得游客授权</label>
      <button
        class="scenic-button primary full-width submit-button"
        type="button"
        :disabled="(!touristFile && !touristSnapshot) || !touristForm.consentGranted"
        @click="submitTouristRegistration"
      >
        <UserPlus :size="16" />
        <span>提交登记</span>
      </button>
    </article>

    <article class="panel recent-panel">
      <div class="panel-head">
        <h3>最近登记</h3>
        <button class="scenic-button compact subtle" type="button" @click="store.refreshWorkspace('scenic')">刷新游客</button>
      </div>
      <ScenicTouristFilters class="registration-filters" />
      <div v-if="store.tourists.length" class="tourist-grid">
        <button
          v-for="tourist in store.tourists"
          :key="tourist.id"
          class="tourist-row"
          :class="{ selected: tourist.id === store.selectedTourist?.id }"
          type="button"
          @click="emit('select-tourist', tourist.id)"
        >
          <img v-if="tourist.entry_image_url" :src="normalizeAssetUrl(tourist.entry_image_url)" alt="" />
          <span v-else class="avatar-fallback"><Users :size="18" /></span>
          <span class="tourist-meta">
            <strong>{{ tourist.visitor_label || tourist.ticket_code || "游客" }}</strong>
            <small v-if="tourist.registration_source === 'visitor'">自助登记 · 制作请求 #{{ tourist.production_request_id }}</small>
            <small>{{ tourist.ticket_code }} · {{ tourist.created_at || "-" }}</small>
          </span>
          <span class="status-badge" :class="scenicStatusClass(tourist.status)">{{ scenicStatusLabel(tourist.status) }}</span>
        </button>
      </div>
      <div v-else class="empty-state">
        <Users :size="22" />
        <span>暂无登记记录</span>
      </div>
    </article>
  </section>
</template>

<style scoped>
.registration-layout { --preview-height: clamp(300px, 46vh, 680px); display: grid; min-height: 100%; grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr) clamp(260px, 17vw, 330px); grid-template-rows: auto minmax(280px, 1fr); gap: 16px; }
.panel { min-width: 0; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 16px; color: #e4e4e7; }
.panel-head,
.button-row { display: flex; align-items: center; gap: 10px; }
.panel-head { justify-content: space-between; margin-bottom: 12px; }
.panel-head h3 { margin: 0; color: #fafafa; font-size: 16px; font-weight: 800; }
.photo-panel { display: flex; flex-direction: column; gap: 12px; }
.photo-panel .panel-head { margin-bottom: 0; }
.preview-stage { display: grid; min-width: 0; flex: 1; place-items: center; }
.entry-drop,
.camera-preview { position: relative; display: grid; min-width: 0; min-height: 0; place-items: center; overflow: hidden; border: 1px dashed rgb(255 255 255 / 0.09); border-radius: 8px; background: rgb(0 0 0 / 0.32); color: #71717a; }
.entry-drop { width: min(100%, var(--preview-height)); aspect-ratio: 1; cursor: pointer; font-size: 16px; }
.camera-preview { width: min(100%, calc(var(--preview-height) * 4 / 3)); aspect-ratio: 4 / 3; }
.entry-drop input,
.file-button input { display: none; }
.entry-drop img,
.camera-preview .camera-live-stream { position: absolute; inset: 0; }
.camera-preview :deep(img) { position: absolute; inset: 0; display: block; width: 100%; height: 100%; min-width: 0; min-height: 0; object-fit: contain; padding: 8px; }
.button-row { margin-top: 12px; }
.photo-panel .button-row { margin-top: 0; }
.registration-form { display: flex; align-self: start; flex-direction: column; gap: 10px; }
.submit-button { margin-top: auto; }
.recent-panel { display: flex; min-height: 280px; grid-column: 1 / -1; flex-direction: column; }
.recent-panel > .empty-state { flex: 1; align-content: center; }
.registration-filters :deep(form) { grid-template-columns: minmax(180px, 1fr) minmax(0, 3fr) auto; align-items: end; }
.registration-filters :deep(.filter-grid) { grid-template-columns: repeat(4, minmax(0, 1fr)); align-items: end; }
.tourist-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.tourist-row { display: flex; min-width: 0; min-height: 66px; align-items: center; gap: 10px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 8px; color: #e4e4e7; text-align: left; }
.tourist-row:hover { border-color: rgb(94 234 212 / 0.28); }
.tourist-row.selected { border-color: rgb(45 212 191 / 0.55); background: rgb(20 184 166 / 0.13); }
.tourist-row img,
.avatar-fallback { width: 46px; height: 46px; flex: 0 0 46px; border-radius: 6px; object-fit: cover; }
.avatar-fallback { display: grid; place-items: center; background: rgb(255 255 255 / 0.07); color: #a1a1aa; }
.tourist-meta { min-width: 0; flex: 1; }
.tourist-meta strong,
.tourist-meta small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tourist-meta strong { font-size: 14px; }
.tourist-meta small { margin-top: 3px; color: #a1a1aa; font-size: 11px; }
.status-badge { display: inline-flex; min-width: 56px; min-height: 24px; align-items: center; justify-content: center; border-radius: 999px; padding: 0 8px; font-size: 11px; font-weight: 800; }
.status-ok { border: 1px solid rgb(52 211 153 / 0.28); background: rgb(22 163 74 / 0.18); color: #bbf7d0; }
.status-warn { border: 1px solid rgb(251 191 36 / 0.28); background: rgb(180 83 9 / 0.2); color: #fde68a; }
.status-danger { border: 1px solid rgb(251 113 133 / 0.3); background: rgb(159 18 57 / 0.22); color: #fecdd3; }
.status-info { border: 1px solid rgb(96 165 250 / 0.3); background: rgb(37 99 235 / 0.18); color: #bfdbfe; }
.text-status { font-size: 12px; font-weight: 700; }
.green { color: #5eead4; }
.red { color: #fb7185; }
.scenic-field { width: 100%; min-height: 38px; border: 1px solid rgb(255 255 255 / 0.12); border-radius: 6px; background: rgb(0 0 0 / 0.24); padding: 8px 10px; color: #fff; outline: none; }
.scenic-field option { background: #18181b; color: #fafafa; }
.scenic-field:focus { border-color: rgb(94 234 212 / 0.58); background: rgb(20 184 166 / 0.08); }
.scenic-field:disabled { background: rgb(255 255 255 / 0.06); color: #71717a; }
.scenic-field::placeholder { color: #71717a; }
.check-line { display: inline-flex; align-items: center; gap: 7px; color: #d4d4d8; font-size: 13px; }
.check-line input { width: 15px; height: 15px; accent-color: #14b8a6; }
.scenic-button { display: inline-flex; min-height: 36px; align-items: center; justify-content: center; gap: 7px; border: 1px solid rgb(255 255 255 / 0.12); border-radius: 8px; padding: 8px 12px; color: #e4e4e7; font-size: 13px; font-weight: 800; transition: transform 180ms ease, border-color 180ms ease, background 180ms ease; }
.scenic-button:hover { transform: translateY(-1px); }
.scenic-button.primary { border-color: rgb(45 212 191 / 0.42); background: linear-gradient(135deg, rgb(20 184 166 / 0.95), rgb(99 102 241 / 0.82)); color: #fff; }
.scenic-button.subtle { background: rgb(255 255 255 / 0.06); }
.scenic-button.subtle:hover { border-color: rgb(94 234 212 / 0.45); background: rgb(20 184 166 / 0.16); }
.scenic-button.compact { min-height: 30px; padding: 5px 9px; font-size: 12px; }
.scenic-button.full-width { width: 100%; }
.scenic-button:disabled { cursor: not-allowed; opacity: 0.45; pointer-events: none; }
.empty-state { display: grid; min-height: 160px; place-items: center; gap: 8px; border: 1px dashed rgb(255 255 255 / 0.07); border-radius: 8px; color: #a1a1aa; }

@media (min-width: 1261px) and (max-height: 1100px) {
  .registration-layout { --preview-height: clamp(260px, 37vh, 680px); }
}
@media (max-width: 1260px) {
  .registration-filters :deep(form) { grid-template-columns: 1fr; }
}
@media (max-width: 1100px) {
  .registration-layout { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }
  .registration-form { grid-column: 1 / -1; }
  .tourist-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 700px) {
  .registration-layout { grid-template-columns: 1fr; }
  .registration-filters :deep(.filter-grid) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tourist-grid { grid-template-columns: 1fr; }
}
</style>
