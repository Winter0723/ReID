<script setup lang="ts">
import type { ScenicPagination } from "../../lib/api/scenic";
defineProps<{ pagination: ScenicPagination; loading?: boolean; label: string }>();
defineEmits<{ (event: "page", page: number): void }>();
</script>
<template>
  <nav class="list-pager" :aria-label="`${label}分页`">
    <span aria-live="polite">{{ loading ? '加载中…' : `共 ${pagination.total} 条 · 第 ${pagination.page} / ${Math.max(1, pagination.total_pages)} 页` }}</span>
    <div><button type="button" :aria-label="`${label}上一页`" :disabled="loading || pagination.page <= 1" @click="$emit('page', pagination.page - 1)">上一页</button><button type="button" :aria-label="`${label}下一页`" :disabled="loading || pagination.page >= pagination.total_pages" @click="$emit('page', pagination.page + 1)">下一页</button></div>
  </nav>
</template>
<style scoped>
.list-pager { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin: 10px 0; color: #a1b6b0; font-size: 11px; }.list-pager div { display: flex; gap: 5px; }.list-pager button { border: 1px solid #36534a; border-radius: 5px; background: #152923; color: #d4e5df; padding: 6px 8px; cursor: pointer; }.list-pager button:disabled { opacity: .4; cursor: not-allowed; }
</style>
