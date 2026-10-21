<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Film, Users, Video } from "@lucide/vue";

import { downloadAsset, normalizeAssetUrl } from "../../lib/api/http";
import type { ScenicVlogJob, ScenicVlogVariant, TouristSegment } from "../../lib/api/scenic";
import { scenicEditingApi, type EditingCatalog } from "../../lib/api/scenicEditing";
import { resolveVideoPreviewSource } from "../../lib/videoPreview";
import ScenicTouristFilters from "./ScenicTouristFilters.vue";
import { useWorkbenchStore } from "../../stores/workbench";
import ScenicPaymentPanel from "../ScenicPaymentPanel.vue";
import ScenicVlogEditor from "./ScenicVlogEditor.vue";
import {
  formatSimilarity,
  scenicStatusClass,
  scenicStatusLabel,
  segmentDuration,
  segmentDurationSeconds,
  segmentVideoAvailable,
} from "./viewHelpers";


const emit = defineEmits<{
  (event: "preview", payload: {
    title: string;
    source?: string;
    mediaType?: string;
    frameSource?: string;
    fallbackSource?: string;
    seekSeconds?: number;
    fallbackSeekSeconds?: number;
    downloadSource?: string;
    downloadName?: string;
  }): void;
}>();

const store = useWorkbenchStore();
const segments = computed(() => store.selectedTourist?.segments || []);
const vlogs = computed(() => store.selectedTourist?.vlog_jobs || []);
const includedCount = computed(() => segments.value.filter((segment) => segment.included !== false).length);
const catalog = ref<EditingCatalog | null>(null);
const catalogError = ref("");
watch(() => store.scenicUser?.username, async (username) => {
  catalog.value = null;
  catalogError.value = "";
  if (!username) return;
  try { const response = await scenicEditingApi.catalog(); if (store.scenicUser?.username === username) catalog.value = response; }
  catch (cause) { if (store.scenicUser?.username === username) catalogError.value = cause instanceof Error ? cause.message : "剪辑服务暂不可用"; }
}, { immediate: true });

async function retryVariant(vlog: ScenicVlogJob, variant: ScenicVlogVariant): Promise<void> {
  const id = store.selectedTourist?.id;
  try { await scenicEditingApi.retry(vlog.id, variant.id); if (id && id === store.selectedTourist?.id) await store.selectTourist(id); }
  catch (cause) { store.error = cause instanceof Error ? cause.message : "重试失败"; }
}
function previewVariant(vlog: ScenicVlogJob, variant: ScenicVlogVariant, staff: boolean): void {
  emit("preview", { title: `版本 #${vlog.id} · ${variant.orientation === 'portrait' ? '竖版' : '横版'} · ${staff ? '工作预览' : '游客试看'}`, source: staff ? variant.draft_preview_url || variant.full_url : variant.preview_url, mediaType: "video", frameSource: variant.cover_url, downloadSource: variant.full_url, downloadName: `vlog_${vlog.id}_${variant.orientation}.mp4` });
}

function jobLabel(status?: string): string {
  if (status === "queued") return "排队";
  if (status === "running") return "处理中";
  if (status === "succeeded") return "完成";
  if (status === "failed") return "失败";
  if (status === "partial") return "部分完成";
  return status || "未知";
}

async function setAllSegmentsIncluded(included: boolean): Promise<void> {
  await store.updateSegments(segments.value.map((segment) => segment.id), included);
}

async function invertSegmentSelection(): Promise<void> {
  const includedIds = segments.value.filter((segment) => segment.included !== false).map((segment) => segment.id);
  const excludedIds = segments.value.filter((segment) => segment.included === false).map((segment) => segment.id);
  if (includedIds.length) await store.updateSegments(includedIds, false);
  if (excludedIds.length) await store.updateSegments(excludedIds, true);
}

function firstSegmentPoster(): string {
  const selected = segments.value.find((segment) => segment.included !== false && segment.thumbnail_url)
    || segments.value.find((segment) => segment.thumbnail_url);
  return selected?.thumbnail_url || "";
}

async function previewSegmentMedia(segment: TouristSegment, full: boolean): Promise<void> {
  const startSeconds = Number(segment.start_seconds || 0);
  const preview = await resolveVideoPreviewSource({
    scope: "scenic",
    projectId: store.scenicProjectId,
    mediaId: segment.media_id,
    fallback: segment.original_media_url || "",
    full,
    startSeconds: full ? 0 : startSeconds,
    durationSeconds: full ? undefined : segmentDurationSeconds(segment),
    generatedSeekSeconds: 0,
    fallbackSeekSeconds: full ? 0 : startSeconds,
  });
  emit("preview", {
    title: full ? `${segment.camera_name || "完整视频"}` : `${segment.camera_name || "片段"} ${segmentDuration(segment)}`,
    source: preview.source,
    mediaType: "video",
    frameSource: segment.thumbnail_url,
    fallbackSource: preview.fallbackSource,
    seekSeconds: preview.seekSeconds,
    fallbackSeekSeconds: preview.fallbackSeekSeconds,
    downloadSource: preview.downloadSource || segment.original_media_url,
    downloadName: `scenic_segment_${segment.id}.mp4`,
  });
}

function previewVlog(vlog: ScenicVlogJob, full = false): void {
  const source = full ? vlog.full_url || vlog.preview_url : vlog.preview_url || vlog.full_url;
  const label = store.selectedTourist?.visitor_label || store.selectedTourist?.ticket_code || "游客";
  emit("preview", {
    title: `${label} ${full ? "完整成片" : "预览成片"}`,
    source,
    mediaType: "video",
    frameSource: firstSegmentPoster(),
    downloadSource: vlog.full_url || vlog.preview_url || "",
    downloadName: full
      ? vlog.full_filename || `scenic_vlog_${vlog.id}.mp4`
      : vlog.preview_filename || `scenic_vlog_preview_${vlog.id}.mp4`,
  });
}

async function downloadScenicAsset(source: string, filename: string): Promise<void> {
  if (!source) return;
  try {
    await downloadAsset(source, filename || "scenic-media", "scenic");
  } catch (error) {
    store.error = error instanceof Error ? error.message : "下载失败";
  }
}
</script>

<template>
  <section class="split-layout vlog-layout">
    <details class="panel selection-panel vlog-tourist-picker" :open="!store.selectedTourist"><summary>切换游客 · {{ store.selectedTourist?.visitor_label || store.selectedTourist?.ticket_code || '请选择游客' }}</summary>
      <div class="panel-head">
        <h3>选择游客</h3>
        <Users :size="18" />
      </div>
      <ScenicTouristFilters search-label="搜索成片游客" />
      <div class="scroll-list">
        <button
          v-for="tourist in store.tourists"
          :key="tourist.id"
          class="tourist-row"
          :class="{ selected: tourist.id === store.selectedTourist?.id }"
          type="button"
          @click="store.selectTourist(tourist.id)"
        >
          <img v-if="tourist.entry_image_url" :src="normalizeAssetUrl(tourist.entry_image_url)" alt="" />
          <span v-else class="avatar-fallback"><Users :size="18" /></span>
          <span class="tourist-meta">
            <strong>{{ tourist.visitor_label || tourist.ticket_code || "游客" }}</strong>
            <small>轨迹 {{ tourist.segment_count || 0 }} 段</small>
          </span>
          <span class="status-badge" :class="scenicStatusClass(tourist.status)">{{ scenicStatusLabel(tourist.status) }}</span>
        </button>
      </div>
    </details>

    <article class="panel detail-panel">
      <div class="match-head">
        <div>
          <h3>{{ store.selectedTourist ? store.selectedTourist.visitor_label || store.selectedTourist.ticket_code : "未选择游客" }}</h3>
          <p>{{ store.selectedTourist ? `可用片段 ${segments.length}` : "从左侧选择游客后生成短片" }}</p>
        </div>
        <button v-if="catalog?.enabled === false || !store.scenicUser" class="scenic-button primary" type="button" :disabled="!store.selectedTourist || includedCount === 0" @click="store.generateVlog()">
          <Film :size="15" />
          <span>生成视频短片</span>
        </button>
      </div>
      <p v-if="catalogError" class="red-text">{{ catalogError }}</p>
      <ScenicVlogEditor v-if="catalog?.enabled" :catalog="catalog" />

      <article v-if="!catalog?.enabled" class="sub-panel">
        <div class="segment-toolbar">
          <strong>用于成片的视频片段</strong>
          <span>已选 {{ includedCount }} / {{ segments.length }}</span>
          <button class="scenic-button compact subtle" type="button" :disabled="!segments.length" @click="setAllSegmentsIncluded(true)">全选</button>
          <button class="scenic-button compact subtle" type="button" :disabled="!segments.length" @click="setAllSegmentsIncluded(false)">清空</button>
          <button class="scenic-button compact subtle" type="button" :disabled="!segments.length" @click="invertSegmentSelection">反选</button>
        </div>
        <div v-if="segments.length" class="horizontal-segments">
          <article v-for="segment in segments" :key="segment.id" class="segment-card" :class="{ included: segment.included !== false }">
            <button class="segment-card-media" type="button" :aria-label="`播放${segment.camera_name || '游玩'}片段`" @click="previewSegmentMedia(segment, false)">
              <img v-if="segment.thumbnail_url" :src="normalizeAssetUrl(segment.thumbnail_url)" alt="" />
              <Video v-else :size="22" />
            </button>
            <strong>{{ segment.camera_name || "未知点位" }} · {{ segmentDuration(segment) }}</strong>
            <span>匹配 {{ formatSimilarity(segment.similarity) }} · {{ segment.timestamp || "-" }}</span>
            <div class="mini-actions">
              <button class="scenic-button compact subtle" type="button" :disabled="!segmentVideoAvailable(segment)" @click.stop="previewSegmentMedia(segment, false)">播放片段</button>
              <button class="scenic-button compact subtle" type="button" :disabled="!segmentVideoAvailable(segment)" @click.stop="previewSegmentMedia(segment, true)">完整视频</button>
            </div>
            <label class="check-line"><input :checked="segment.included !== false" type="checkbox" @change="store.updateSegment(segment.id, segment.included === false, segment.sort_order || 0)" />用于成片</label>
          </article>
        </div>
        <div v-else class="empty-inline">暂无匹配片段</div>
      </article>

      <div class="vlog-list">
        <article v-for="vlog in vlogs" :key="vlog.id" class="vlog-card" :class="{ 'dual-vlog': vlog.variants?.length }">
          <template v-if="vlog.variants?.length">
            <div class="dual-head"><h4>成片版本 #{{ vlog.id }}</h4><span>草稿 #{{ vlog.draft_id }} · 修订 {{ vlog.draft_revision }} · {{ jobLabel(vlog.status) }}</span></div>
            <div class="variant-grid"><section v-for="variant in vlog.variants" :key="variant.id" class="variant-card"><div class="variant-preview"><img v-if="variant.cover_url" :src="normalizeAssetUrl(variant.cover_url)" alt="成片封面" /><Film v-else :size="25" /><span>{{ variant.orientation === 'portrait' ? '竖版分享短片 · 9:16' : '横版游览纪念片 · 16:9' }}</span></div><strong>{{ jobLabel(variant.status) }} · {{ (variant.duration_seconds || 0).toFixed(1) }} 秒</strong><progress v-if="variant.status !== 'succeeded'" :value="variant.progress || 0" :max="(variant.progress || 0) > 1 ? 100 : 1" :aria-label="`${variant.orientation} 生成进度`" /><p v-if="variant.message">{{ variant.message }}</p><p v-if="variant.error" class="red-text">{{ variant.error }}</p><div class="button-row"><button class="scenic-button compact primary" type="button" :disabled="!variant.draft_preview_url && !variant.full_url" @click="previewVariant(vlog, variant, true)">工作预览</button><button class="scenic-button compact subtle" type="button" :disabled="!variant.preview_url" @click="previewVariant(vlog, variant, false)">游客试看</button><button class="scenic-button compact subtle" type="button" :disabled="!variant.full_url" @click="downloadScenicAsset(variant.full_url || '', `vlog_${vlog.id}_${variant.orientation}.mp4`)">下载成片</button><button v-if="variant.status === 'failed'" class="scenic-button compact subtle" type="button" @click="retryVariant(vlog, variant)">重试此画幅</button></div></section></div>
          </template>
          <template v-else>
          <div class="vlog-preview">
            <video v-if="vlog.preview_url" :src="normalizeAssetUrl(vlog.preview_url)" :poster="normalizeAssetUrl(firstSegmentPoster())" controls muted></video>
            <span v-else>{{ vlog.status === "failed" ? "生成失败" : "等待生成" }}</span>
          </div>
          <div class="vlog-meta">
            <h4>短片任务 #{{ vlog.id }}</h4>
            <p>状态 {{ jobLabel(vlog.status) }} · 片段 {{ vlog.segment_count || 0 }} · 时长 {{ vlog.duration_seconds || 0 }}s</p>
            <p v-if="vlog.error" class="red-text">{{ vlog.error }}</p>
            <div class="button-row">
              <button class="scenic-button primary" type="button" :disabled="!vlog.preview_url" @click="previewVlog(vlog, false)">预览</button>
              <button class="scenic-button subtle" type="button" :disabled="!vlog.full_url && !vlog.preview_url" @click="previewVlog(vlog, true)">完整观看</button>
              <button class="scenic-button subtle" type="button" :disabled="!vlog.full_url && !vlog.preview_url" @click="downloadScenicAsset(vlog.full_url || vlog.preview_url || '', vlog.full_filename || vlog.preview_filename || `scenic_vlog_${vlog.id}.mp4`)">下载视频</button>
            </div>
          </div>
          </template>
        </article>
        <div v-if="!vlogs.length" class="empty-state large">
          <Film :size="28" />
          <h3>{{ store.selectedTourist ? "暂无短片任务" : "请选择游客" }}</h3>
          <p>生成后可在这里预览、下载并完成支付交付。</p>
        </div>
      </div>

      <ScenicPaymentPanel />
    </article>
  </section>
</template>

<style scoped>
.split-layout { display: grid; grid-template-columns: 310px minmax(0, 1fr); gap: 16px; }
.panel,
.sub-panel { border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); color: #e4e4e7; }
.panel { padding: 16px; }
.sub-panel { margin-top: 16px; padding: 12px; }
.panel-head,
.match-head,
.segment-toolbar,
.button-row,
.mini-actions { display: flex; align-items: center; gap: 10px; }
.panel-head { justify-content: space-between; margin-bottom: 12px; }
.panel-head h3,
.match-head h3,
.match-head p,
.vlog-meta h4,
.vlog-meta p,
.empty-state h3,
.empty-state p { margin: 0; }
.panel-head h3,
.match-head h3,
.vlog-meta h4 { color: #fafafa; }
.selection-panel { min-height: 620px; }
.scroll-list { display: grid; max-height: 580px; gap: 8px; overflow: auto; }
.tourist-row { display: flex; min-width: 0; min-height: 66px; align-items: center; gap: 10px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 8px; color: #e4e4e7; text-align: left; }
.tourist-row:hover { border-color: rgb(94 234 212 / 0.28); }
.tourist-row.selected,
.segment-card.included { border-color: rgb(45 212 191 / 0.55); background: rgb(20 184 166 / 0.13); }
.tourist-row img,
.avatar-fallback { width: 46px; height: 46px; flex: 0 0 46px; border-radius: 6px; object-fit: cover; }
.avatar-fallback { display: grid; place-items: center; background: rgb(255 255 255 / 0.07); color: #a1a1aa; }
.tourist-meta { min-width: 0; flex: 1; }
.tourist-meta strong,
.tourist-meta small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tourist-meta small { margin-top: 3px; color: #a1a1aa; font-size: 11px; }
.status-badge { display: inline-flex; min-width: 56px; min-height: 24px; align-items: center; justify-content: center; border-radius: 999px; padding: 0 8px; font-size: 11px; font-weight: 800; }
.status-ok { border: 1px solid rgb(52 211 153 / 0.28); background: rgb(22 163 74 / 0.18); color: #bbf7d0; }
.status-warn { border: 1px solid rgb(251 191 36 / 0.28); background: rgb(180 83 9 / 0.2); color: #fde68a; }
.status-danger { border: 1px solid rgb(251 113 133 / 0.3); background: rgb(159 18 57 / 0.22); color: #fecdd3; }
.status-info { border: 1px solid rgb(96 165 250 / 0.3); background: rgb(37 99 235 / 0.18); color: #bfdbfe; }
.detail-panel { min-width: 0; }
.tourist-search { width: 100%; margin-bottom: 12px; padding: 9px; border: 1px solid #334540; border-radius: 6px; background: #101918; color: #ecfdf5; }
.dual-vlog { display: block !important; }.dual-head { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; margin-bottom: 14px; }.dual-head h4 { margin: 0; }.dual-head span { color: #a1a1aa; font-size: 11px; }.variant-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 14px; }.variant-card { min-width: 0; padding: 10px; border: 1px solid #294038; border-radius: 8px; }.variant-preview { position: relative; display: grid; height: 135px; place-items: center; overflow: hidden; background: #0a1410; border-radius: 5px; margin-bottom: 9px; }.variant-preview img { width: 100%; height: 100%; object-fit: contain; }.variant-preview span { position: absolute; bottom: 8px; left: 8px; padding: 5px; background: #06110dda; font-size: 11px; color: #a7f3d0; }.variant-card strong { font-size: 12px; }.variant-card p { margin: 7px 0; color: #a1a1aa; font-size: 11px; }.variant-card progress { width: 100%; accent-color: #14b8a6; margin: 8px 0; }.variant-card .button-row { flex-wrap: wrap; margin-top: 9px; }
.match-head > div:first-child { min-width: 0; flex: 1; }
.match-head h3 { overflow: hidden; font-size: 17px; text-overflow: ellipsis; white-space: nowrap; }
.match-head p { margin-top: 4px; color: #a1a1aa; font-size: 12px; }
.segment-toolbar { flex-wrap: wrap; margin-bottom: 12px; }
.segment-toolbar strong { margin-right: auto; }
.segment-toolbar span { color: #a1a1aa; font-size: 12px; }
.horizontal-segments { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
.segment-card { display: grid; width: 250px; flex: 0 0 250px; gap: 7px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 9px; }
.segment-card-media { display: grid; width: 100%; height: 120px; place-items: center; overflow: hidden; border: 1px solid rgb(255 255 255 / 0.09); border-radius: 8px; background: rgb(0 0 0 / 0.32); color: #71717a; }
.segment-card-media img { width: 100%; height: 100%; object-fit: cover; }
.segment-card > strong,
.segment-card > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.segment-card > span { color: #a1a1aa; font-size: 11px; }
.mini-actions { gap: 6px; }
.check-line { display: inline-flex; align-items: center; gap: 7px; color: #d4d4d8; font-size: 13px; }
.check-line input { width: 15px; height: 15px; accent-color: #14b8a6; }
.empty-inline { display: grid; min-height: 120px; place-items: center; border: 1px dashed rgb(255 255 255 / 0.07); border-radius: 8px; color: #71717a; }
.vlog-list { display: grid; gap: 12px; margin: 16px 0; }
.vlog-card { display: grid; min-height: 180px; grid-template-columns: 280px minmax(0, 1fr); gap: 14px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 12px; }
.vlog-preview { display: grid; min-height: 150px; place-items: center; overflow: hidden; border-radius: 8px; background: rgb(0 0 0 / 0.32); color: #71717a; }
.vlog-preview video { width: 100%; height: 100%; object-fit: contain; }
.vlog-meta { min-width: 0; }
.vlog-meta p { margin-top: 8px; color: #a1a1aa; font-size: 12px; }
.vlog-meta .button-row { margin-top: 16px; flex-wrap: wrap; }
.red-text { color: #fb7185 !important; }
.scenic-button { display: inline-flex; min-height: 36px; align-items: center; justify-content: center; gap: 7px; border: 1px solid rgb(255 255 255 / 0.12); border-radius: 8px; background: transparent; padding: 8px 12px; color: #e4e4e7; font-size: 13px; font-weight: 800; }
.scenic-button.primary { border-color: rgb(45 212 191 / 0.42); background: linear-gradient(135deg, rgb(20 184 166 / 0.95), rgb(99 102 241 / 0.82)); color: #fff; }
.scenic-button.subtle { background: rgb(255 255 255 / 0.06); }
.scenic-button.compact { min-height: 30px; padding: 5px 9px; font-size: 12px; }
.scenic-button:disabled { cursor: not-allowed; opacity: 0.45; pointer-events: none; }
.empty-state { display: grid; min-height: 260px; place-items: center; gap: 8px; border: 1px dashed rgb(255 255 255 / 0.07); border-radius: 8px; color: #a1a1aa; text-align: center; }
.empty-state.large { min-height: 390px; }

@media (max-width: 1260px) {
  .split-layout { grid-template-columns: 1fr; }
  .selection-panel { min-height: auto; }
  .scroll-list { max-height: 240px; }
}
@media(max-width:650px) { .variant-grid,.vlog-card { grid-template-columns: 1fr; }.match-head { flex-wrap: wrap; } }
</style>
