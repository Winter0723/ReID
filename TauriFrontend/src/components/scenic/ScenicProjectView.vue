<script setup lang="ts">
import { reactive, ref, toRef } from "vue";
import { FolderPlus, X } from "@lucide/vue";

import ModelSelect from "../ModelSelect.vue";
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
const reidModelId = ref("");
const projectForm = reactive({ name: "默认景区项目", description: "景区游客游玩记录与短片交付", cameraSource: "0" });

function close(): void {
  emit("close");
}

useDialogFocus(toRef(props, "open"), dialog, close);

async function createProject(): Promise<void> {
  await store.createProject("scenic", projectForm.name, projectForm.description, "scenic", projectForm.cameraSource, reidModelId.value);
  if (!store.error) close();
}

function selectProject(projectId: number): void {
  void store.setProject("scenic", projectId);
  close();
}
</script>

<template>
  <div v-if="open" class="modal-layer" @click="close">
    <div
      ref="dialog"
      class="dialog-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="scenic-project-dialog-title"
      tabindex="-1"
      @click.stop
    >
      <div class="panel-head">
        <h3 id="scenic-project-dialog-title">景区项目</h3>
        <button class="icon-square" type="button" title="关闭" aria-label="关闭项目窗口" @click="close"><X :size="16" /></button>
      </div>
      <div class="project-list">
        <button
          v-for="project in store.scenicProjects"
          :key="project.id"
          class="project-row"
          :class="{ selected: project.id === store.scenicProjectId }"
          type="button"
          @click="selectProject(project.id)"
        >
          <strong>{{ project.name }}</strong>
          <span>{{ project.reid_model?.name || "模型待核验" }} · {{ project.project_type }} · {{ project.created_at }}</span>
        </button>
        <div v-if="!store.scenicProjects.length" class="empty-inline">暂无景区项目</div>
      </div>
      <div class="section-rule"></div>
      <input v-model="projectForm.name" class="scenic-field" placeholder="景区名称" />
      <input v-model="projectForm.description" class="scenic-field" placeholder="项目描述" />
      <input v-model="projectForm.cameraSource" class="scenic-field" placeholder="默认相机源：设备 ID，如 0 或 entrance" />
      <ModelSelect v-model="reidModelId" scope="scenic" :open="open" />
      <button class="scenic-button primary full-width" type="button" :disabled="!store.isAuthenticated || !reidModelId" @click="createProject">
        <FolderPlus :size="16" />
        <span>创建景区项目</span>
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
  width: min(520px, 94vw);
  max-height: calc(100vh - 48px);
  gap: 14px;
  overflow-y: auto;
  border: 1px solid rgb(255 255 255 / 0.1);
  border-radius: 12px;
  background: #18181b;
  padding: 24px;
  color: #e4e4e7;
  box-shadow: 0 24px 80px rgb(0 0 0 / 0.46);
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-head h3 {
  margin: 0;
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

.project-list {
  display: grid;
  max-height: 220px;
  gap: 8px;
  overflow-y: auto;
}

.project-row {
  display: grid;
  gap: 4px;
  border: 1px solid rgb(255 255 255 / 0.08);
  border-radius: 9px;
  background: rgb(255 255 255 / 0.04);
  padding: 12px;
  color: #d4d4d8;
  text-align: left;
}

.project-row.selected {
  border-color: rgb(34 211 238 / 0.48);
  background: rgb(34 211 238 / 0.1);
  color: #ecfeff;
}

.project-row span,
.empty-inline {
  color: #a1a1aa;
  font-size: 12px;
}

.empty-inline {
  padding: 16px;
  text-align: center;
}

.section-rule {
  height: 1px;
  background: rgb(255 255 255 / 0.08);
}

.scenic-field {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid rgb(255 255 255 / 0.1);
  border-radius: 8px;
  outline: none;
  background: rgb(9 9 11 / 0.7);
  padding: 10px 12px;
  color: #f4f4f5;
}

.scenic-field:focus {
  border-color: rgb(34 211 238 / 0.55);
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

.scenic-button.primary {
  border-color: rgb(34 211 238 / 0.28);
  background: rgb(8 145 178 / 0.2);
  color: #cffafe;
}

.full-width {
  width: 100%;
}
</style>
