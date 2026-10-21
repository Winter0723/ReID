<script setup lang="ts">
import { ref, toRef } from "vue";
import { LogOut, Settings2, X } from "@lucide/vue";

import { useDialogFocus } from "../../lib/dialogFocus";
import { useWorkbenchStore } from "../../stores/workbench";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "close"): void;
}>();

const store = useWorkbenchStore();
const dialog = ref<HTMLElement | null>(null);

function close(): void {
  emit("close");
}

useDialogFocus(toRef(props, "open"), dialog, close);

function logout(): void {
  store.logout("scenic");
  close();
}
</script>

<template>
  <div v-if="open && store.isAuthenticated" class="modal-layer" @click="close">
    <div
      ref="dialog"
      class="dialog-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="scenic-account-dialog-title"
      tabindex="-1"
      @click.stop
    >
      <div class="panel-head">
        <div>
          <p class="modal-eyebrow">Account & Settings</p>
          <h3 id="scenic-account-dialog-title">个人信息与系统设置</h3>
        </div>
        <button class="icon-square" type="button" title="关闭" aria-label="关闭账户窗口" @click="close"><X :size="16" /></button>
      </div>

      <div class="profile-summary">
        <div class="profile-avatar">{{ (store.scenicUser?.username || "U").slice(0, 1).toUpperCase() }}</div>
        <div class="profile-copy">
          <strong>{{ store.scenicUser?.username || "-" }}</strong>
          <span>{{ store.scenicUser?.email || "未绑定邮箱" }}</span>
        </div>
        <span class="status-badge">{{ store.scenicUser?.role || "operator" }}</span>
      </div>

      <div class="profile-rows">
        <div><span>用户 ID</span><strong>{{ store.scenicUser?.public_id || "-" }}</strong></div>
        <div><span>账号状态</span><strong>{{ store.scenicUser?.is_active === false ? "已停用" : "正常" }}</strong></div>
        <div><span>创建时间</span><strong>{{ store.scenicUser?.created_at || "-" }}</strong></div>
      </div>

      <div class="section-rule"></div>
      <div class="settings-block">
        <h4><Settings2 :size="16" />系统设置</h4>
        <label class="setting-row">
          <span><strong>自动刷新实时画面</strong><small>关闭后不再自动更新摄像头画面和任务进度</small></span>
          <input type="checkbox" :checked="store.preferences.autoRefresh" @change="store.updatePreferences({ autoRefresh: ($event.target as HTMLInputElement).checked })" />
        </label>
        <label class="setting-row">
          <span><strong>刷新间隔</strong><small>在画面流畅度和本机负载之间调整</small></span>
          <select
            class="scenic-field setting-select"
            :value="store.preferences.livePollIntervalMs"
            @change="store.updatePreferences({ livePollIntervalMs: Number(($event.target as HTMLSelectElement).value) })"
          >
            <option :value="500">0.5 秒</option>
            <option :value="1000">1 秒</option>
            <option :value="2000">2 秒</option>
            <option :value="5000">5 秒</option>
          </select>
        </label>
      </div>

      <button class="scenic-button danger full-width" type="button" @click="logout">
        <LogOut :size="15" />
        <span>退出景区账号</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.modal-layer {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: grid;
  place-items: center;
  overflow-y: auto;
  background: rgb(0 0 0 / 0.68);
  padding: 24px;
}

.dialog-card {
  display: grid;
  width: min(560px, 94vw);
  max-height: calc(100vh - 48px);
  gap: 16px;
  overflow-y: auto;
  border: 1px solid rgb(255 255 255 / 0.1);
  border-radius: 12px;
  background: #18181b;
  padding: 24px;
  color: #e4e4e7;
  box-shadow: 0 24px 80px rgb(0 0 0 / 0.46);
}

.panel-head,
.profile-summary,
.profile-rows > div,
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-head h3,
.modal-eyebrow,
.settings-block h4 {
  margin: 0;
}

.modal-eyebrow {
  color: #67e8f9;
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.icon-square {
  display: inline-grid;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid rgb(255 255 255 / 0.12);
  border-radius: 8px;
  background: rgb(255 255 255 / 0.06);
  color: #d4d4d8;
}

.profile-summary {
  border: 1px solid rgb(255 255 255 / 0.08);
  border-radius: 10px;
  background: rgb(255 255 255 / 0.04);
  padding: 14px;
}

.profile-avatar {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: rgb(8 145 178 / 0.24);
  color: #cffafe;
  font-size: 18px;
  font-weight: 800;
}

.profile-copy {
  min-width: 0;
  flex: 1;
}

.profile-copy strong,
.profile-copy span,
.setting-row strong,
.setting-row small {
  display: block;
}

.profile-copy span,
.profile-rows span,
.setting-row small {
  color: #a1a1aa;
  font-size: 12px;
}

.status-badge {
  border-radius: 999px;
  background: rgb(16 185 129 / 0.14);
  padding: 5px 9px;
  color: #a7f3d0;
  font-size: 11px;
  font-weight: 700;
}

.profile-rows {
  display: grid;
  gap: 8px;
}

.profile-rows > div {
  border-bottom: 1px solid rgb(255 255 255 / 0.06);
  padding: 8px 0;
  font-size: 12px;
}

.profile-rows strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-rule {
  height: 1px;
  background: rgb(255 255 255 / 0.08);
}

.settings-block {
  display: grid;
  gap: 10px;
}

.settings-block h4 {
  display: flex;
  align-items: center;
  gap: 8px;
}

.setting-row {
  min-height: 48px;
}

.setting-row > span {
  min-width: 0;
  flex: 1;
}

.scenic-field {
  border: 1px solid rgb(255 255 255 / 0.1);
  border-radius: 8px;
  outline: none;
  background: rgb(9 9 11 / 0.7);
  padding: 8px 10px;
  color: #f4f4f5;
}

.setting-select {
  width: 116px;
}

.scenic-button {
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 9px 13px;
  font-weight: 700;
}

.scenic-button.danger {
  border-color: rgb(244 63 94 / 0.28);
  background: rgb(244 63 94 / 0.1);
  color: #fecdd3;
}

.full-width {
  width: 100%;
}
</style>
