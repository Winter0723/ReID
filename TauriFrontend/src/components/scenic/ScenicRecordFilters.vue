<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useWorkbenchStore } from "../../stores/workbench";
import { dateBounds, localDate } from "./listFilters";
import ScenicListPager from "./ScenicListPager.vue";
const props = defineProps<{ kind: "history" | "audit" }>();
const store = useWorkbenchStore();
const label = computed(() => props.kind === "history" ? "检索历史" : "操作审计");
const query = computed(() => props.kind === "history" ? store.scenicHistoryQuery : store.scenicAuditQuery);
const pagination = computed(() => props.kind === "history" ? store.scenicHistoryPagination : store.scenicAuditPagination);
const loading = computed(() => props.kind === "history" ? store.scenicHistoryLoading : store.scenicAuditLoading);
const loadError = computed(() => props.kind === "history" ? store.scenicHistoryError : store.scenicAuditError);
const form = reactive({ q: "", start: "", end: "", action: "" });
const error = ref("");
watch(query, (value) => { Object.assign(form, { q: value.q, start: localDate(value.date_from), end: localDate(value.date_to, true), action: "action" in value ? value.action : "" }); }, { immediate: true, deep: true });
async function load(page: number): Promise<void> {
  error.value = "";
  try {
    const patch = { q: form.q.trim(), ...dateBounds(form.start, form.end), page };
    if (props.kind === "history") { store.scenicHistoryQuery = { ...store.scenicHistoryQuery, ...patch }; await store.loadSearchHistory("scenic"); }
    else { store.scenicAuditQuery = { ...store.scenicAuditQuery, ...patch, action: form.action.trim() }; await store.loadAuditLogs("scenic"); }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "筛选失败"; }
}
function reset(): void { Object.assign(form, { q: "", start: "", end: "", action: "" }); void load(1); }
</script>
<template>
  <section class="record-filters" :aria-label="`${label}筛选`">
    <form @submit.prevent="load(1)"><input v-model="form.q" type="search" :aria-label="`${label}关键词`" placeholder="搜索当前项目记录" /><input v-if="kind === 'audit'" v-model="form.action" aria-label="审计操作类型" placeholder="操作类型（如 tourist.register）" /><label>开始日期<input v-model="form.start" type="date" :aria-label="`${label}开始日期`" /></label><label>结束日期<input v-model="form.end" type="date" :min="form.start || undefined" :aria-label="`${label}结束日期`" /></label><button type="submit" :disabled="loading">查询记录</button><button type="button" :disabled="loading" @click="reset">重置</button></form>
    <p v-if="error || loadError" role="alert">{{ error || loadError }}</p>
    <ScenicListPager :pagination="pagination" :loading="loading" :label="label" @page="load($event)" />
  </section>
</template>
<style scoped>
.record-filters { border: 1px solid #283b33; border-radius: 8px; background: #0f1915; padding: 12px; }.record-filters form { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }.record-filters input { width: 100%; min-width: 0; padding: 8px; color: #e0eae5; background: #111f1a; border: 1px solid #344c44; border-radius: 5px; color-scheme: dark; font-size: 12px; }.record-filters form>input { flex: 1 1 190px; width: auto; }.record-filters label { flex: 0 1 160px; display: grid; gap: 4px; color: #9fb1a9; font-size: 11px; }.record-filters button { border: 1px solid #36584d; border-radius: 5px; background: #18382d; padding: 8px 10px; color: #d4ebe0; cursor: pointer; }.record-filters button:disabled { opacity: .4; cursor: not-allowed; }.record-filters p { color: #fecdd3; font-size: 12px; }
</style>
