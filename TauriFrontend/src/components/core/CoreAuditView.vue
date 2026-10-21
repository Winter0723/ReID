<script setup lang="ts">
import { computed } from "vue";
import { Activity, History, RefreshCw, X } from "@lucide/vue";

import { useWorkbenchStore } from "../../stores/workbench";

const emit = defineEmits<{
  (event: "close"): void;
}>();

const store = useWorkbenchStore();
const emptyText = computed(() => {
  if (!store.isAuthenticated) return "登录后可查看当前项目的审计记录";
  if (store.activeUser?.role !== "admin" && !store.coreProjectId) return "请先创建或选择项目，再查看项目审计记录";
  return "暂无审计记录";
});
</script>

<template>
  <header class="flex h-[72px] shrink-0 items-center justify-between border-b border-white/[0.06] px-8">
    <div>
      <p class="mb-0.5 text-[10px] uppercase tracking-[0.2em] text-amber-500/80">Observability</p>
      <h1 class="text-lg font-bold tracking-tight text-white/90">审计日志与历史事件</h1>
    </div>
    <div class="flex items-center gap-2">
      <button class="ghost-button rounded-lg border-white/[0.06] bg-zinc-900/40 px-4 py-1.5 text-xs" type="button" @click="store.loadAuditLogs('core')">
        <RefreshCw :size="13" />
        <span>重新拉取</span>
      </button>
      <button class="icon-button h-8 w-8 rounded-lg" type="button" title="关闭" aria-label="关闭审计日志" @click="emit('close')">
        <X :size="14" />
      </button>
    </div>
  </header>

  <div class="flex-1 overflow-y-auto p-8">
    <div class="space-y-3">
      <div
        v-for="(row, idx) in store.auditRows"
        :key="idx"
        class="glass-panel flex flex-col gap-4 p-4 transition-all hover:bg-zinc-900/60 sm:flex-row sm:items-center"
      >
        <div class="flex w-48 shrink-0 items-center gap-3">
          <div class="flex h-8 w-8 items-center justify-center rounded bg-zinc-800 text-zinc-400"><Activity :size="14" /></div>
          <span class="font-mono text-xs text-zinc-400">{{ row.timestamp || "-" }}</span>
        </div>
        <div class="min-w-0 flex-1">
          <h4 class="mb-1 text-sm font-bold text-white">{{ row.event || "系统事件" }}</h4>
          <p class="truncate text-xs text-zinc-500">{{ row.details || row.message || "-" }}</p>
        </div>
        <div class="flex shrink-0 items-center gap-2 text-xs">
          <span class="rounded border border-zinc-700 bg-zinc-800 px-2 py-1 font-mono text-zinc-400">{{ row.level || "INFO" }}</span>
        </div>
      </div>

      <div v-if="!store.auditRows.length" class="empty-panel h-64 rounded-xl border border-dashed border-white/[0.06] bg-zinc-900/20">
        <History :size="24" class="mb-3 text-zinc-600" />
        <span class="text-xs text-zinc-500">{{ emptyText }}</span>
      </div>
    </div>
  </div>
</template>
