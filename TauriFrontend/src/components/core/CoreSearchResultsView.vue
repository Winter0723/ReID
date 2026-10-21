<script setup lang="ts">
import { computed } from "vue";
import { Download, Eye, FileImage, Filter, Image, Route, Search, Sparkles, Type } from "@lucide/vue";

import { normalizeAssetUrl } from "../../lib/api/http";
import type { SearchResult } from "../../lib/api/search";
import { useWorkbenchStore } from "../../stores/workbench";

const props = defineProps<{
  fallbackQueryText?: string;
  fallbackQueryImageUrl?: string;
  fallbackQueryKind?: "text" | "image" | "";
}>();

const emit = defineEmits<{
  (event: "preview-frame", result: SearchResult): void;
  (event: "preview-media", result: SearchResult, seekToHit: boolean): void;
  (event: "download", result: SearchResult): void;
}>();

const store = useWorkbenchStore();

const queryKind = computed<"text" | "image">(() => {
  const type = String(store.searchResponse?.task?.type || props.fallbackQueryKind || "text").toLowerCase();
  return type.includes("image") || type.includes("图") ? "image" : "text";
});

const queryImageUrl = computed(() => normalizeAssetUrl(store.searchResponse?.query_object_url) || props.fallbackQueryImageUrl || "");
const queryText = computed(() => store.searchResponse?.task?.content || props.fallbackQueryText || "尚未执行文本检索");
const filterSummary = computed(() => store.searchResponse?.task?.filter_summary || "未设置额外筛选条件");
const resultCount = computed(() => store.searchResponse?.pagination?.total ?? store.searchResults.length);

function resultImage(result: SearchResult): string {
  return normalizeAssetUrl(result.thumbnail_url || result.path);
}

function sourceUrl(result: SearchResult): string {
  return normalizeAssetUrl(result.original_media_url || result.original_media_path || result.thumbnail_url || result.path);
}

function isVideoResult(result: SearchResult): boolean {
  const text = `${result.media_type || ""} ${result.original_media_url || result.original_media_path || result.media_filename || ""}`.toLowerCase();
  return text.includes("video") || text.includes("camera") || /\.(mp4|mov|avi|mkv|webm)(\?|#|$)/i.test(text);
}

function formatSimilarity(value?: number): string {
  return `${Math.round((value || 0) * 100)}%`;
}
</script>

<template>
  <header class="flex h-[72px] shrink-0 items-center justify-between border-b border-white/[0.06] px-8">
    <div>
      <p class="mb-0.5 text-[10px] uppercase tracking-[0.2em] text-cyan-500/80">Search Results</p>
      <h1 class="text-lg font-bold tracking-tight text-white/90">检索结果</h1>
    </div>
    <button
      class="ghost-button rounded-lg border-white/[0.06] bg-zinc-900/40 px-4 py-1.5 text-xs"
      type="button"
      :disabled="!store.searchResponse?.task_id"
      @click="store.exportCurrentSearchReport()"
    >
      <Download :size="13" />
      <span>导出报告</span>
    </button>
  </header>

  <div class="search-results-scroll flex-1 overflow-y-auto p-6 xl:p-8">
    <div class="search-results-layout">
      <details class="query-rail">
        <summary>本次检索 · {{ queryKind === 'image' ? '图像查询' : '文本查询' }} · {{ resultCount }} 个命中</summary>
        <div class="query-heading">
          <div class="query-icon" :class="queryKind">
            <Image v-if="queryKind === 'image'" :size="18" />
            <Type v-else :size="18" />
          </div>
          <div>
            <span>本次检索内容</span>
            <strong>{{ queryKind === "image" ? "图像查询" : "文本查询" }}</strong>
          </div>
        </div>

        <div v-if="queryKind === 'image'" class="query-image-frame">
          <img v-if="queryImageUrl" :src="queryImageUrl" alt="本次检索使用的查询图片" />
          <div v-else class="query-empty"><Image :size="24" /><span>查询图片不可用</span></div>
        </div>
        <blockquote v-else class="query-copy">{{ queryText }}</blockquote>

        <div class="query-meta">
          <div>
            <span><Filter :size="12" />筛选条件</span>
            <p>{{ filterSummary }}</p>
          </div>
          <div class="result-count">
            <span>命中数量</span>
            <strong>{{ resultCount }}</strong>
          </div>
        </div>
      </details>

      <section class="results-main">
        <div class="section-title-row">
          <div>
            <span>Result Matrix</span>
            <h2><Sparkles :size="16" />命中结果</h2>
          </div>
          <p v-if="store.searchResponse">已按相似度从高到低排列</p>
        </div>

        <div v-if="store.searchResults.length" class="result-grid">
          <button
            v-for="result in store.searchResults"
            :key="result.id || result.rank"
            class="result-tile"
            :class="{ selected: store.selectedResult?.id === result.id }"
            type="button"
            @click="store.selectedResult = result"
          >
            <div class="result-image-frame">
              <img :src="resultImage(result)" alt="" />
              <span>#{{ result.rank || "-" }}</span>
            </div>
            <div class="result-tile-meta">
              <strong>{{ formatSimilarity(result.similarity) }}</strong>
              <span>{{ result.timestamp || result.media_filename || "-" }}</span>
            </div>
          </button>
        </div>

        <div v-else class="results-empty">
          <Search :size="26" />
          <strong>{{ store.searchResponse ? "本次检索暂无命中" : "等待执行检索" }}</strong>
          <span>{{ store.searchResponse ? "请确认媒体已完成入库，或调整相似度与筛选条件。" : "从左侧控制面板发起文本或图像检索，结果会自动显示在这里。" }}</span>
        </div>

        <article v-if="store.selectedResult" class="result-detail">
          <div class="detail-preview">
            <img :src="resultImage(store.selectedResult)" alt="当前选中的命中结果" />
            <span>当前命中 · #{{ store.selectedResult.rank || store.selectedResult.id || "-" }}</span>
          </div>
          <div class="detail-body">
            <div class="detail-heading">
              <div>
                <span>Selected Match</span>
                <h3>详细命中画像</h3>
              </div>
              <strong>{{ formatSimilarity(store.selectedResult.similarity) }}</strong>
            </div>

            <dl class="detail-list">
              <div><dt>源媒体</dt><dd>{{ store.selectedResult.media_filename || "-" }}</dd></div>
              <div><dt>摄像头节点</dt><dd>{{ store.selectedResult.camera_id || "-" }}</dd></div>
              <div><dt>时间戳</dt><dd>{{ store.selectedResult.timestamp || "-" }}</dd></div>
            </dl>

            <div class="detail-actions">
              <button class="ghost-button" type="button" @click="emit('preview-frame', store.selectedResult)">
                <FileImage :size="14" />
                <span>{{ isVideoResult(store.selectedResult) ? "查看命中帧" : "查看图片" }}</span>
              </button>
              <button
                v-if="isVideoResult(store.selectedResult)"
                class="ghost-button"
                type="button"
                :disabled="!store.selectedResult.original_media_url && !store.selectedResult.original_media_path"
                @click="emit('preview-media', store.selectedResult, true)"
              >
                <Route :size="14" />
                <span>查看视频帧位置</span>
              </button>
              <button
                class="primary-button"
                type="button"
                :disabled="!store.selectedResult.original_media_url && !store.selectedResult.original_media_path"
                @click="emit('preview-media', store.selectedResult, false)"
              >
                <Eye :size="14" />
                <span>{{ isVideoResult(store.selectedResult) ? "查看完整视频" : "查看原图" }}</span>
              </button>
              <button
                class="ghost-button"
                type="button"
                :disabled="!sourceUrl(store.selectedResult)"
                @click="emit('download', store.selectedResult)"
              >
                <Download :size="14" />
                <span>{{ isVideoResult(store.selectedResult) ? "下载完整视频" : "下载原图" }}</span>
              </button>
            </div>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>

<style scoped>
.search-results-scroll { scrollbar-gutter: stable; }
.search-results-layout { display: grid; grid-template-columns: minmax(230px, 300px) minmax(0, 1fr); gap: 24px; align-items: start; }
.query-rail { position: sticky; top: 0; display: grid; gap: 18px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 14px; background: linear-gradient(160deg, rgb(24 24 27 / 0.72), rgb(9 9 11 / 0.74)); padding: 18px; box-shadow: 0 18px 60px rgb(0 0 0 / 0.28); }
.query-heading { display: flex; align-items: center; gap: 12px; }
.query-heading > div:last-child { display: grid; gap: 2px; }
.query-heading span,
.section-title-row span,
.detail-heading span { color: #71717a; font-size: 10px; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; }
.query-heading strong { color: #f4f4f5; font-size: 14px; }
.query-icon { display: grid; width: 38px; height: 38px; place-items: center; border: 1px solid rgb(192 132 252 / 0.24); border-radius: 10px; background: rgb(147 51 234 / 0.12); color: #d8b4fe; }
.query-icon.image { border-color: rgb(34 211 238 / 0.26); background: rgb(6 182 212 / 0.12); color: #a5f3fc; }
.query-image-frame { aspect-ratio: 1 / 1; overflow: hidden; border: 1px solid rgb(255 255 255 / 0.08); border-radius: 12px; background: #09090b; }
.query-image-frame img { width: 100%; height: 100%; object-fit: contain; }
.query-empty { display: grid; height: 100%; place-items: center; align-content: center; gap: 8px; color: #52525b; font-size: 11px; }
.query-copy { margin: 0; border-left: 2px solid rgb(192 132 252 / 0.58); border-radius: 0 10px 10px 0; background: rgb(147 51 234 / 0.08); padding: 16px; color: #e4e4e7; font-size: 15px; line-height: 1.7; overflow-wrap: anywhere; }
.query-meta { display: grid; gap: 12px; border-top: 1px solid rgb(255 255 255 / 0.06); padding-top: 14px; }
.query-meta > div:first-child span { display: flex; align-items: center; gap: 6px; color: #a1a1aa; font-size: 11px; }
.query-meta p { margin: 7px 0 0; color: #71717a; font-size: 11px; line-height: 1.55; }
.result-count { display: flex; align-items: end; justify-content: space-between; border-radius: 10px; background: rgb(255 255 255 / 0.035); padding: 10px 12px; }
.result-count span { color: #a1a1aa; font-size: 11px; }
.result-count strong { color: #67e8f9; font-size: 24px; line-height: 1; }
.results-main { min-width: 0; }
.section-title-row { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.section-title-row h2 { display: flex; align-items: center; gap: 8px; margin: 3px 0 0; color: #f4f4f5; font-size: 15px; }
.section-title-row h2 svg { color: #22d3ee; }
.section-title-row p { margin: 0; color: #71717a; font-size: 11px; }
.result-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.result-tile { min-width: 0; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 12px; background: rgb(24 24 27 / 0.4); padding: 8px; color: inherit; text-align: left; transition: transform 180ms ease, border-color 180ms ease, background 180ms ease; }
.result-tile:hover { transform: translateY(-2px); border-color: rgb(34 211 238 / 0.3); background: rgb(24 24 27 / 0.72); }
.result-tile.selected { border-color: rgb(34 211 238 / 0.55); background: rgb(8 145 178 / 0.1); box-shadow: 0 0 0 1px rgb(34 211 238 / 0.12), 0 14px 30px rgb(0 0 0 / 0.24); }
.result-image-frame { position: relative; aspect-ratio: 1 / 1; overflow: hidden; border-radius: 9px; background: #09090b; }
.result-image-frame img { width: 100%; height: 100%; object-fit: cover; }
.result-image-frame span { position: absolute; top: 7px; left: 7px; border-radius: 6px; background: #22d3ee; padding: 3px 6px; color: #082f49; font-size: 10px; font-weight: 900; }
.result-tile-meta { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 3px 2px; }
.result-tile-meta strong { color: #67e8f9; font-size: 12px; }
.result-tile-meta span { min-width: 0; overflow: hidden; color: #71717a; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.results-empty { display: grid; min-height: 300px; place-items: center; align-content: center; gap: 9px; border: 1px dashed rgb(255 255 255 / 0.08); border-radius: 14px; background: rgb(24 24 27 / 0.22); color: #52525b; text-align: center; }
.results-empty strong { color: #a1a1aa; font-size: 13px; }
.results-empty span { max-width: 380px; color: #71717a; font-size: 11px; line-height: 1.6; }
.result-detail { display: grid; grid-template-columns: minmax(180px, 250px) minmax(0, 1fr); gap: 20px; margin-top: 24px; border: 1px solid rgb(255 255 255 / 0.07); border-radius: 14px; background: rgb(24 24 27 / 0.45); padding: 18px; }
.detail-preview { position: relative; min-height: 240px; overflow: hidden; border-radius: 11px; background: #09090b; }
.detail-preview img { width: 100%; height: 100%; object-fit: cover; }
.detail-preview > span { position: absolute; right: 8px; bottom: 8px; left: 8px; border: 1px solid rgb(255 255 255 / 0.1); border-radius: 7px; background: rgb(0 0 0 / 0.66); padding: 7px 9px; color: #d4d4d8; font-size: 10px; backdrop-filter: blur(8px); }
.detail-body { display: grid; min-width: 0; align-content: start; gap: 16px; }
.detail-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.detail-heading h3 { margin: 3px 0 0; color: #fff; font-size: 16px; }
.detail-heading > strong { color: #67e8f9; font-size: 26px; }
.detail-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin: 0; }
.detail-list > div { min-width: 0; border-radius: 9px; background: rgb(9 9 11 / 0.52); padding: 10px; }
.detail-list dt { color: #71717a; font-size: 10px; }
.detail-list dd { margin: 5px 0 0; overflow: hidden; color: #e4e4e7; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.detail-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.detail-actions button { min-height: 38px; border-radius: 9px; padding: 8px 10px; font-size: 11px; }

@media (max-width: 1460px) {
  .search-results-layout { grid-template-columns: 230px minmax(0, 1fr); gap: 18px; }
  .result-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .result-detail { grid-template-columns: 190px minmax(0, 1fr); }
  .detail-list { grid-template-columns: 1fr; }
}

@media (max-width: 1120px) {
  .search-results-layout { grid-template-columns: 1fr; }
  .query-rail { position: static; grid-template-columns: minmax(170px, 220px) minmax(0, 1fr); align-items: start; }
  .query-heading,
  .query-meta { grid-column: 2; }
  .query-image-frame,
  .query-copy { grid-column: 1; grid-row: 1 / span 2; }
  .query-copy { min-height: 150px; }
}

@media (max-width: 820px) {
  .query-rail { display: grid; grid-template-columns: 1fr; }
  .query-heading,
  .query-meta,
  .query-image-frame,
  .query-copy { grid-column: 1; grid-row: auto; }
  .result-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .result-detail { grid-template-columns: 1fr; }
  .detail-preview { min-height: 280px; }
}
</style>
