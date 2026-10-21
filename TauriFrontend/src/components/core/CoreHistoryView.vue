<script setup lang="ts">
import { History, RefreshCw, X } from "@lucide/vue";

import { normalizeAssetUrl } from "../../lib/api/http";
import type { SearchHistoryItem, SearchResult } from "../../lib/api/search";
import { useWorkbenchStore } from "../../stores/workbench";

const emit = defineEmits<{
  (event: "close"): void;
  (event: "open-results"): void;
}>();

const store = useWorkbenchStore();

function resultImage(result: SearchResult): string {
  return normalizeAssetUrl(result.thumbnail_url || result.path);
}

function historyThumbnails(item: SearchHistoryItem): SearchResult[] {
  return (item.results || []).filter((result) => result.thumbnail_url || result.path).slice(0, 5);
}

async function openHistoryItem(item: SearchHistoryItem, result?: SearchResult): Promise<void> {
  await store.loadHistoryPage(item.id, 1, "core");
  if (result) store.selectedResult = store.searchResults.find((row) => row.id === result.id) || result;
  emit("open-results");
}
</script>

<template>
  <header class="flex h-[72px] shrink-0 items-center justify-between border-b border-white/[0.06] px-8">
    <div>
      <p class="mb-0.5 text-[10px] uppercase tracking-[0.2em] text-amber-500/80">Search History</p>
      <h1 class="text-lg font-bold tracking-tight text-white/90">历史检索任务</h1>
    </div>
    <div class="flex items-center gap-2">
      <button class="ghost-button rounded-lg border-white/[0.06] bg-zinc-900/40 px-4 py-1.5 text-xs" type="button" @click="store.loadSearchHistory('core')">
        <RefreshCw :size="13" />
        <span>重新拉取</span>
      </button>
      <button class="icon-button h-8 w-8 rounded-lg" type="button" title="关闭" aria-label="关闭检索历史" @click="emit('close')">
        <X :size="14" />
      </button>
    </div>
  </header>

  <div class="flex-1 overflow-y-auto p-8">
    <div class="space-y-3">
      <div
        v-for="item in store.searchHistory"
        :key="item.id"
        class="glass-panel flex cursor-pointer flex-col gap-4 p-4 transition-all hover:bg-zinc-900/60 sm:flex-row sm:items-center"
        @click="openHistoryItem(item)"
      >
        <div class="flex w-48 shrink-0 items-center gap-3">
          <div class="flex h-8 w-8 items-center justify-center rounded bg-zinc-800 text-zinc-400"><History :size="14" /></div>
          <span class="font-mono text-xs text-zinc-400">{{ item.created_at || "-" }}</span>
        </div>
        <div class="min-w-0 flex-1">
          <h4 class="mb-1 text-sm font-bold text-white">检索记录 #{{ item.id }}</h4>
          <p class="truncate text-xs text-zinc-500">类型: {{ item.query_type || "Unknown" }} | 目标内容: {{ item.query_content || "N/A" }}</p>
        </div>
        <div class="flex shrink-0 items-center gap-2 text-xs">
          <button
            v-for="thumb in historyThumbnails(item)"
            :key="thumb.id || thumb.rank"
            class="h-14 w-14 overflow-hidden rounded-lg border border-white/[0.06] bg-zinc-950 transition hover:border-cyan-400/60"
            type="button"
            :aria-label="`查看检索记录 ${item.id} 的结果 ${thumb.rank || thumb.id || ''}`"
            @click.stop="openHistoryItem(item, thumb)"
          >
            <img :src="resultImage(thumb)" class="h-full w-full object-cover" alt="" />
          </button>
          <span class="rounded border border-zinc-700 bg-zinc-800 px-2 py-1 font-mono text-zinc-400">{{ item.type || "历史" }}</span>
        </div>
      </div>

      <div v-if="!store.searchHistory.length" class="empty-panel h-64 rounded-xl border border-dashed border-white/[0.06] bg-zinc-900/20">
        <History :size="24" class="mb-3 text-zinc-600" />
        <span class="text-xs text-zinc-500">暂无搜索历史记录</span>
      </div>
    </div>
  </div>
</template>
