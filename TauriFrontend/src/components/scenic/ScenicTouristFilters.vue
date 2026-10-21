<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { ScenicWorkQueue } from "../../lib/api/scenic";
import { useWorkbenchStore } from "../../stores/workbench";
import { dateBounds, localDate } from "./listFilters";
import ScenicListPager from "./ScenicListPager.vue";
withDefaults(defineProps<{ searchLabel?: string }>(), { searchLabel: "搜索游客" });
const store = useWorkbenchStore();
const form = reactive({ q: "", status: "", work_queue: "" as ScenicWorkQueue | "", start: "", end: "" });
const error = ref("");
const queues: { value: ScenicWorkQueue; label: string }[] = [
  { value: "needs_review", label: "待复核" }, { value: "needs_vlog", label: "待成片" },
  { value: "failed", label: "失败待处理" }, { value: "awaiting_payment", label: "待付款" }, { value: "ready_delivery", label: "待领取" },
];
watch(() => store.touristQuery, (query) => {
  Object.assign(form, { q: query.q, status: query.status, work_queue: query.work_queue, start: localDate(query.date_from), end: localDate(query.date_to, true) });
}, { immediate: true, deep: true });
const selectedOutsidePage = computed(() => store.selectedTourist && !store.tourists.some((tourist) => tourist.id === store.selectedTourist?.id));
async function apply(): Promise<void> {
  error.value = "";
  try { await store.loadScenicTourists({ q: form.q.trim(), status: form.status, work_queue: form.work_queue, ...dateBounds(form.start, form.end), page: 1 }); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "筛选失败"; }
}
function reset(): void { Object.assign(form, { q: "", status: "", work_queue: "", start: "", end: "" }); void apply(); }
</script>
<template>
  <div class="tourist-filters" :aria-busy="store.touristLoading">
    <form @submit.prevent="apply">
      <input v-model="form.q" type="search" :aria-label="searchLabel" placeholder="票号、游客备注或联系方式" maxlength="120" />
      <div class="filter-grid">
        <select v-model="form.work_queue" aria-label="工作待办筛选" @change="apply"><option value="">全部工作待办</option><option v-for="queue in queues" :key="queue.value" :value="queue.value">{{ queue.label }}{{ store.touristQueueCounts[queue.value] === undefined ? '' : `（${store.touristQueueCounts[queue.value]}）` }}</option></select>
        <select v-model="form.status" aria-label="游客状态筛选" @change="apply"><option value="">全部游客状态</option><option value="registered">已登记</option><option value="checked_out">已出园</option><option value="matched">已匹配</option><option value="no_match">无匹配</option><option value="rendering">成片中</option><option value="vlog_partial">双版部分完成</option><option value="vlog_failed">成片失败</option><option value="vlog_ready">成片就绪</option><option value="paid">已付款</option></select>
        <label>登记起日<input v-model="form.start" type="date" aria-label="游客登记开始日期" /></label>
        <label>登记止日<input v-model="form.end" type="date" :min="form.start || undefined" aria-label="游客登记结束日期" /></label>
      </div>
      <div class="filter-actions"><button type="submit" :disabled="store.touristLoading">查找游客</button><button type="button" :disabled="store.touristLoading" @click="reset">重置筛选</button></div>
    </form>
    <p v-if="error || store.touristError" class="filter-error" role="alert">{{ error || store.touristError }}<button v-if="store.touristError" type="button" @click="store.loadScenicTourists()">重试</button></p>
    <ScenicListPager :pagination="store.touristPagination" :loading="store.touristLoading" label="游客" @page="store.loadScenicTourists({ page: $event })" />
    <p v-if="!store.touristLoading && !store.touristError && !store.tourists.length" class="filter-note">没有符合条件的游客，可调整筛选或重置。</p>
    <p v-if="selectedOutsidePage" class="filter-note">当前操作：{{ store.selectedTourist?.visitor_label || store.selectedTourist?.ticket_code }}（不在本页，编辑仍保留）</p>
  </div>
</template>
<style scoped>
.tourist-filters { margin-bottom: 12px; min-width: 0; }.tourist-filters form { display: grid; gap: 8px; }.filter-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 7px; }.tourist-filters input,.tourist-filters select { width: 100%; min-width: 0; min-height: 34px; padding: 7px; border: 1px solid #344c44; border-radius: 6px; background: #101d19; color: #e0eae5; font-size: 11px; color-scheme: dark; }.filter-grid label { display: grid; gap: 4px; font-size: 10px; color: #9ab3a7; }.filter-actions { display: flex; gap: 7px; }.tourist-filters button { border: 1px solid #36584d; border-radius: 5px; background: #18382d; padding: 7px 9px; color: #d4ebe0; cursor: pointer; font-size: 11px; }.tourist-filters button:disabled { opacity: .45; cursor: not-allowed; }.filter-note,.filter-error { margin: 8px 0; font-size: 11px; line-height: 1.6; color: #a8bcb3; }.filter-error { color: #fecdd3; }.filter-error button { margin-left: 8px; }
</style>
