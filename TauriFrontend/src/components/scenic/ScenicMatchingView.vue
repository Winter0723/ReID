<script setup lang="ts">
import { computed, ref } from "vue";
import { Film, Route, Search, Users, Video } from "@lucide/vue";

import { normalizeAssetUrl } from "../../lib/api/http";
import { scenicApi, type TouristSegment } from "../../lib/api/scenic";
import ScenicTouristFilters from "./ScenicTouristFilters.vue";
import { useWorkbenchStore } from "../../stores/workbench";
import ScenicReferenceSelector from "./ScenicReferenceSelector.vue";
import {
  formatSimilarity,
  scenicStatusClass,
  scenicStatusLabel,
  segmentDuration,
} from "./viewHelpers";


const emit = defineEmits<{
  (event: "navigate-vlog"): void;
  (event: "preview", payload: {
    title: string;
    source?: string;
    mediaType?: string;
    downloadName?: string;
  }): void;
}>();

const store = useWorkbenchStore();
const minSimilarity = ref(0.2);
async function checkout(): Promise<void> {
  const id = store.selectedTourist?.id;
  if (!id) return;
  try { await scenicApi.checkout(id); if (store.selectedTourist?.id === id) await store.selectTourist(id); }
  catch (cause) { store.error = cause instanceof Error ? cause.message : "出园确认失败"; }
}
const segments = computed(() => store.selectedTourist?.segments || []);
const includedCount = computed(() => segments.value.filter((segment) => segment.included !== false).length);

async function setAllSegmentsIncluded(included: boolean): Promise<void> {
  await store.updateSegments(segments.value.map((segment) => segment.id), included);
}

async function invertSegmentSelection(): Promise<void> {
  const includedIds = segments.value.filter((segment) => segment.included !== false).map((segment) => segment.id);
  const excludedIds = segments.value.filter((segment) => segment.included === false).map((segment) => segment.id);
  if (includedIds.length) await store.updateSegments(includedIds, false);
  if (excludedIds.length) await store.updateSegments(excludedIds, true);
}

function previewSegmentFrame(segment: TouristSegment): void {
  emit("preview", {
    title: `${segment.camera_name || segment.camera_id || "游玩片段"} 命中帧`,
    source: segment.thumbnail_url,
    mediaType: "image",
    downloadName: `scenic_segment_${segment.id}.jpg`,
  });
}
</script>

<template>
  <section class="split-layout matching-layout">
    <ScenicTouristFilters class="matching-filters" search-label="搜索匹配游客" />
    <aside class="panel selection-panel">
      <div class="panel-head">
        <h3>游客</h3>
        <button class="scenic-button compact subtle" type="button" @click="store.refreshWorkspace('scenic')">刷新</button>
      </div>
      <div v-if="store.selectedTourist" class="current-tourist"><img v-if="store.selectedTourist.entry_image_url" :src="normalizeAssetUrl(store.selectedTourist.entry_image_url)" alt="当前游客登记照" /><strong>{{ store.selectedTourist.visitor_label || store.selectedTourist.ticket_code }}</strong><small>{{ store.selectedTourist.ticket_code }}</small></div>
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
            <small>{{ tourist.ticket_code }} · {{ tourist.created_at || "-" }}</small>
          </span>
          <span class="status-badge" :class="scenicStatusClass(tourist.status)">{{ scenicStatusLabel(tourist.status) }}</span>
        </button>
      </div>
    </aside>

    <article class="panel detail-panel">
      <div class="match-head">
        <img v-if="store.selectedTourist?.entry_image_url" :src="normalizeAssetUrl(store.selectedTourist.entry_image_url)" alt="" />
        <div>
          <h3>{{ store.selectedTourist ? store.selectedTourist.visitor_label || store.selectedTourist.ticket_code : "请选择游客" }}</h3>
          <p>{{ store.selectedTourist ? `票号 ${store.selectedTourist.ticket_code} · ${scenicStatusLabel(store.selectedTourist.status)}` : "选择后可基于入园照片匹配监控视频片段" }}</p>
        </div>
        <label class="threshold-box">
          <span>阈值</span>
          <input v-model.number="minSimilarity" max="1" min="0" step="0.05" type="number" />
        </label>
        <button class="scenic-button subtle" type="button" :disabled="!store.selectedTourist || Boolean(store.selectedTourist.checked_out_at)" @click="checkout">{{ store.selectedTourist?.checked_out_at ? '已确认出园' : '确认出园' }}</button>
        <button class="scenic-button primary" type="button" :disabled="!store.selectedTourist" @click="store.matchSelectedTourist(minSimilarity)">
          <Search :size="15" />
          <span>开始匹配</span>
        </button>
        <button class="scenic-button subtle" type="button" :disabled="!store.selectedTourist" @click="emit('navigate-vlog')">
          <Film :size="15" />
          <span>流转短片</span>
        </button>
      </div>

      <div class="section-rule"></div>
      <ScenicReferenceSelector />

      <div class="segment-toolbar">
        <span>已选 {{ includedCount }} / {{ segments.length }}</span>
        <button class="scenic-button compact subtle" type="button" :disabled="!segments.length" @click="setAllSegmentsIncluded(true)">全选</button>
        <button class="scenic-button compact subtle" type="button" :disabled="!segments.length" @click="setAllSegmentsIncluded(false)">清空</button>
        <button class="scenic-button compact subtle" type="button" :disabled="!segments.length" @click="invertSegmentSelection">反选</button>
      </div>

      <div v-if="segments.length" class="segment-list">
        <article v-for="segment in segments" :key="segment.id" class="segment-row">
          <button class="segment-thumb" type="button" :aria-label="`查看${segment.camera_name || '游玩片段'}命中帧`" @click="previewSegmentFrame(segment)">
            <img v-if="segment.thumbnail_url" :src="normalizeAssetUrl(segment.thumbnail_url)" alt="" />
            <Video v-else :size="22" />
          </button>
          <div class="segment-info">
            <strong>{{ segment.camera_name || segment.camera_id || "未知点位" }}</strong>
            <span>时间 {{ segment.timestamp || "-" }} · 匹配 {{ formatSimilarity(segment.similarity) }}</span>
            <span>片段 {{ segmentDuration(segment) }}</span>
            <span :class="segment.included !== false ? 'green-text' : 'muted-text'">{{ segment.included !== false ? "已加入成片" : "不用于成片" }}</span>
          </div>
          <div class="segment-actions">
            <button class="scenic-button compact subtle" type="button" :disabled="!segment.thumbnail_url" @click="previewSegmentFrame(segment)">查看命中帧</button>
            <label class="check-line"><input :checked="segment.included !== false" type="checkbox" @change="store.updateSegment(segment.id, segment.included === false, segment.sort_order || 0)" />用于成片</label>
          </div>
        </article>
      </div>
      <div v-else class="empty-state large">
        <Route :size="28" />
        <h3>{{ store.selectedTourist ? "暂无匹配片段" : "请选择游客" }}</h3>
        <p>执行匹配后，会按点位和时间生成候选游玩片段。</p>
      </div>
    </article>
    <aside class="panel review-summary"><h3>复核摘要</h3><div class="review-count"><strong>{{ includedCount }} 个精彩片段</strong><span>已选 / 全部 {{ includedCount }} / {{ segments.length }}</span></div><p class="surface-caption">逐一查看候选画面，取消不需要的片段，再进入短片制作。</p><dl><div><dt>当前游客</dt><dd>{{ store.selectedTourist?.visitor_label || store.selectedTourist?.ticket_code || '尚未选择' }}</dd></div><div><dt>已排除</dt><dd>{{ segments.length - includedCount }} 段</dd></div></dl><button class="scenic-button primary full-width" type="button" :disabled="!store.selectedTourist || !includedCount" @click="emit('navigate-vlog')">确认并制作短片 →</button><small>保留原始素材，复核后仍可调整。</small></aside>
  </section>
</template>

<style scoped>
.split-layout { display: grid; grid-template-columns: 310px minmax(0, 1fr); gap: 16px; }
.panel { border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 16px; color: #e4e4e7; }
.panel-head,
.match-head,
.segment-toolbar { display: flex; align-items: center; gap: 10px; }
.panel-head { justify-content: space-between; margin-bottom: 12px; }
.panel-head h3,
.match-head h3,
.match-head p,
.empty-state h3,
.empty-state p { margin: 0; }
.panel-head h3,
.match-head h3 { color: #fafafa; }
.selection-panel { min-height: 620px; }
.scroll-list,
.segment-list { display: grid; gap: 8px; }
.scroll-list { max-height: 580px; overflow: auto; }
.tourist-row { display: flex; min-width: 0; min-height: 66px; align-items: center; gap: 10px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 8px; color: #e4e4e7; text-align: left; }
.tourist-row:hover { border-color: rgb(94 234 212 / 0.28); }
.tourist-row.selected { border-color: rgb(45 212 191 / 0.55); background: rgb(20 184 166 / 0.13); }
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
.match-head { flex-wrap: wrap; }
.match-head > img { width: 64px; height: 64px; border-radius: 8px; object-fit: cover; }
.match-head > div:first-of-type { min-width: 0; flex: 1; }
.match-head h3 { overflow: hidden; font-size: 17px; text-overflow: ellipsis; white-space: nowrap; }
.match-head p { margin-top: 4px; color: #a1a1aa; font-size: 12px; }
.threshold-box { display: flex; width: 92px; min-height: 38px; align-items: center; gap: 6px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 0 8px; color: #d4d4d8; font-size: 12px; }
.threshold-box input { width: 48px; border: 0; background: transparent; color: #fff; outline: none; }
.section-rule { height: 1px; margin: 16px 0; background: rgb(255 255 255 / 0.08); }
.segment-toolbar { flex-wrap: wrap; margin-bottom: 12px; }
.segment-toolbar > span { margin-right: auto; color: #a1a1aa; font-size: 12px; }
.segment-row { display: grid; min-height: 112px; grid-template-columns: 118px minmax(0, 1fr) auto; align-items: center; gap: 14px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 8px; background: rgb(9 9 11 / 0.38); padding: 10px; }
.segment-thumb { display: grid; width: 118px; height: 88px; place-items: center; overflow: hidden; border: 1px solid rgb(255 255 255 / 0.09); border-radius: 8px; background: rgb(0 0 0 / 0.32); color: #71717a; }
.segment-thumb img { width: 100%; height: 100%; object-fit: cover; }
.segment-info { min-width: 0; }
.segment-info strong,
.segment-info span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.segment-info span { margin-top: 4px; color: #a1a1aa; font-size: 12px; }
.segment-actions { display: grid; justify-items: end; gap: 10px; }
.green-text { color: #5eead4 !important; }
.muted-text { color: #71717a !important; }
.check-line { display: inline-flex; align-items: center; gap: 7px; color: #d4d4d8; font-size: 13px; }
.check-line input { width: 15px; height: 15px; accent-color: #14b8a6; }
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
</style>
