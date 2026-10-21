<script setup lang="ts">
import ScenicRecordFilters from "./ScenicRecordFilters.vue";
import { Activity, RefreshCw } from "@lucide/vue";
import { useWorkbenchStore } from "../../stores/workbench";

const store = useWorkbenchStore();
</script>

<template>
  <section class="audit-layout">
    <div class="toolbar-row">
      <h3>操作审计</h3>
      <button class="refresh-button" type="button" @click="store.loadAuditLogs('scenic')">
        <RefreshCw :size="15" />
        <span>刷新审计</span>
      </button>
    </div>
    <ScenicRecordFilters kind="audit" />
    <div class="audit-list">
      <article
        v-for="(row, index) in store.auditRows"
        :key="`${row.id || index}-${row.timestamp || row.created_at || ''}`"
        class="audit-row"
      >
        <div class="audit-icon">审</div>
        <div>
          <strong>{{ row.action || row.event || "operation" }}</strong>
          <span>{{ row.username || "" }} · {{ row.resource_type || "" }} {{ row.resource_id || "" }} · {{ row.created_at || row.timestamp || "" }}</span>
        </div>
        <p :title="String(row.detail || row.details || row.message || '')">{{ row.detail || row.details || row.message || "" }}</p>
      </article>
      <div v-if="!store.auditRows.length && !store.scenicAuditLoading && !store.scenicAuditError" class="empty-state">
        <Activity :size="28" />
        <h3>暂无审计记录</h3>
        <p>景区账号、项目、游客、匹配和交付动作都会独立写入景区审计。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.audit-layout,
.audit-list {
  display: grid;
  gap: 12px;
}

.toolbar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.toolbar-row h3 {
  margin: 0;
  color: #e4e4e7;
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid rgb(255 255 255 / 0.12);
  border-radius: 8px;
  background: rgb(255 255 255 / 0.06);
  padding: 8px 11px;
  color: #e4e4e7;
}

.audit-row {
  display: grid;
  min-height: 72px;
  grid-template-columns: 44px minmax(0, 1fr) minmax(160px, 360px);
  align-items: center;
  gap: 14px;
  border: 1px solid rgb(255 255 255 / 0.07);
  border-radius: 8px;
  background: rgb(9 9 11 / 0.38);
  padding: 12px;
  color: #e4e4e7;
}

.audit-icon {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border-radius: 8px;
  background: rgb(20 184 166 / 0.16);
  color: #99f6e4;
  font-weight: 800;
}

.audit-row strong,
.audit-row span,
.audit-row p {
  display: block;
  overflow: hidden;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-row span,
.audit-row p {
  color: #a1a1aa;
  font-size: 12px;
}

.empty-state {
  display: grid;
  min-height: 260px;
  place-items: center;
  gap: 8px;
  border: 1px dashed rgb(255 255 255 / 0.07);
  border-radius: 8px;
  background: rgb(9 9 11 / 0.38);
  padding: 24px;
  color: #a1a1aa;
  text-align: center;
}

.empty-state h3,
.empty-state p {
  margin: 0;
}
@media (max-width: 700px) { .audit-row { grid-template-columns: 36px minmax(0, 1fr); }.audit-row p { grid-column: 1 / -1; white-space: normal; overflow-wrap: anywhere; } }
</style>
