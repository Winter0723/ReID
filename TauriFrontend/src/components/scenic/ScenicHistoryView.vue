<script setup lang="ts">
import ScenicRecordFilters from "./ScenicRecordFilters.vue";
import { History, Users } from "@lucide/vue";
import { normalizeAssetUrl } from "../../lib/api/http";
import type { SearchHistoryItem, SearchResult } from "../../lib/api/search";
import ScenicTouristFilters from "./ScenicTouristFilters.vue";
import { useWorkbenchStore } from "../../stores/workbench";
import { formatSimilarity, scenicStatusClass, scenicStatusLabel } from "./viewHelpers";

const emit = defineEmits<{
  (event: "select-tourist", touristId: number): void;
  (event: "preview", payload: {
    title: string;
    source?: string;
    mediaType?: string;
    downloadName?: string;
  }): void;
}>();

const store = useWorkbenchStore();

function historyThumbnails(item: SearchHistoryItem): SearchResult[] {
  return (item.results || []).filter((result) => result.thumbnail_url || result.path).slice(0, 8);
}

function historyQueryImage(item: SearchHistoryItem): string {
  return normalizeAssetUrl(item.query_object_url || "");
}

function resultImage(result: SearchResult): string {
  return normalizeAssetUrl(result.thumbnail_url || result.path);
}

</script>

<template>
  <section class="history-layout">
    <article class="view-panel">
      <div class="panel-head">
        <h3>游客记录</h3>
        <span>{{ store.touristPagination.total }} 条</span>
      </div>
      <ScenicTouristFilters />
      <div class="tourist-strip">
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
    </article>

    <ScenicRecordFilters kind="history" />
    <div class="history-list">
      <article v-for="item in store.searchHistory" :key="item.id" class="history-card">
        <div class="query-thumb">
          <img v-if="historyQueryImage(item)" :src="historyQueryImage(item)" alt="" />
          <span v-else>Query</span>
        </div>
        <div class="history-meta">
          <h3>{{ item.query_content || item.content || "景区检索" }}</h3>
          <p>{{ item.created_at || "-" }}</p>
          <strong>结果 {{ item.result_count || item.results?.length || 0 }}</strong>
        </div>
        <div class="thumb-strip">
          <button
            v-for="thumb in historyThumbnails(item)"
            :key="thumb.id || thumb.rank"
            type="button"
            @click="emit('preview', {
              title: `历史命中 #${thumb.rank || thumb.id || '-'}`,
              source: thumb.thumbnail_url || thumb.path,
              mediaType: 'image',
              downloadName: `scenic_history_${thumb.id || thumb.rank || 'frame'}.jpg`,
            })"
          >
            <img :src="resultImage(thumb)" alt="" />
            <span>#{{ thumb.rank || "-" }} {{ formatSimilarity(thumb.similarity) }}</span>
          </button>
        </div>
      </article>
      <div v-if="!store.searchHistory.length && !store.scenicHistoryLoading && !store.scenicHistoryError" class="empty-state">
        <History :size="28" />
        <h3>暂无游客检索历史</h3>
        <p>执行出园匹配后，景区检索历史会单独记录在这里。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.history-layout,
.history-list {
  display: grid;
  gap: 12px;
}

.view-panel,
.history-card,
.tourist-row,
.empty-state {
  border: 1px solid rgb(255 255 255 / 0.07);
  border-radius: 8px;
  background: rgb(9 9 11 / 0.38);
  color: #e4e4e7;
}

.view-panel {
  padding: 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.panel-head h3 {
  margin: 0;
}

.tourist-strip,
.thumb-strip {
  display: flex;
  gap: 10px;
  overflow-x: auto;
}

.tourist-row {
  display: grid;
  width: 310px;
  min-height: 66px;
  flex: 0 0 310px;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 10px;
  text-align: left;
}

.tourist-row.selected {
  border-color: rgb(45 212 191 / 0.55);
  background: rgb(20 184 166 / 0.13);
}

.tourist-row img,
.avatar-fallback {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  object-fit: cover;
}

.avatar-fallback {
  display: grid;
  place-items: center;
  background: rgb(255 255 255 / 0.07);
  color: #a1a1aa;
}

.tourist-meta strong,
.tourist-meta small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tourist-meta small {
  margin-top: 4px;
  color: #a1a1aa;
  font-size: 11px;
}

.status-badge {
  border-radius: 999px;
  padding: 4px 7px;
  font-size: 11px;
}

.status-ok { background: rgb(22 163 74 / 0.18); color: #bbf7d0; }
.status-warn { background: rgb(180 83 9 / 0.2); color: #fde68a; }
.status-danger { background: rgb(159 18 57 / 0.22); color: #fecdd3; }
.status-info { background: rgb(37 99 235 / 0.18); color: #bfdbfe; }

.history-card {
  display: grid;
  min-height: 150px;
  grid-template-columns: 110px 260px minmax(0, 1fr);
  gap: 14px;
  padding: 12px;
}

.query-thumb {
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: rgb(0 0 0 / 0.32);
  color: #71717a;
}

.query-thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  padding: 6px;
}

.history-meta h3,
.history-meta p {
  margin: 0;
}

.history-meta h3 {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta p {
  margin-top: 8px;
  color: #a1a1aa;
  font-size: 12px;
}

.history-meta strong {
  display: block;
  margin-top: 10px;
  color: #93c5fd;
}

.thumb-strip button {
  width: 96px;
  height: 126px;
  flex: 0 0 96px;
  border: 1px solid rgb(255 255 255 / 0.09);
  border-radius: 6px;
  background: rgb(0 0 0 / 0.32);
  padding: 6px;
  color: #e4e4e7;
}

.thumb-strip img {
  width: 100%;
  height: 84px;
  border-radius: 4px;
  object-fit: cover;
}

.thumb-strip span {
  display: block;
  margin-top: 5px;
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  display: grid;
  min-height: 260px;
  place-items: center;
  gap: 8px;
  border-style: dashed;
  padding: 24px;
  color: #a1a1aa;
  text-align: center;
}

.empty-state h3,
.empty-state p {
  margin: 0;
}

@media (max-width: 1180px) {
  .history-card { grid-template-columns: 90px minmax(180px, 0.5fr) minmax(0, 1fr); }
}
@media (max-width: 700px) { .history-card { grid-template-columns: 70px minmax(0, 1fr); }.thumb-strip { grid-column: 1 / -1; } }
</style>
