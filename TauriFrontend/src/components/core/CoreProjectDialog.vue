<script setup lang="ts">
import { reactive, ref, toRef } from "vue";
import { FolderPlus, RefreshCw, X } from "@lucide/vue";

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
const projectForm = reactive({ name: "基础分析项目", description: "", projectType: "media", cameraSource: "0" });

function close(): void {
  emit("close");
}

useDialogFocus(toRef(props, "open"), dialog, close);

async function createProject(): Promise<void> {
  await store.createProject("core", projectForm.name, projectForm.description, projectForm.projectType, projectForm.cameraSource, reidModelId.value);
  if (!store.error) close();
}

function selectProject(projectId: number): void {
  void store.setProject("core", projectId);
  close();
}
</script>

<template>
  <div v-if="open" class="modal-overlay" @click="close">
    <div
      ref="dialog"
      class="modal-content"
      role="dialog"
      aria-modal="true"
      aria-labelledby="core-project-dialog-title"
      tabindex="-1"
      @click.stop
    >
      <div class="mb-6 flex items-center justify-between">
        <h2 id="core-project-dialog-title" class="text-lg font-bold text-white">项目管理</h2>
        <button class="text-zinc-500 transition-colors hover:text-white" type="button" title="关闭" aria-label="关闭项目窗口" @click="close">
          <X :size="18" />
        </button>
      </div>

      <div class="space-y-4">
        <div class="flex items-center justify-between">
          <p class="text-xs uppercase tracking-widest text-zinc-500">已选项目</p>
          <button class="flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300" type="button" @click="store.loadProjects('core')">
            <RefreshCw :size="12" /> 同步
          </button>
        </div>
        <div class="max-h-48 space-y-2 overflow-y-auto pr-1">
          <button
            v-for="project in store.activeProjects"
            :key="project.id"
            class="flex w-full items-center justify-between rounded-xl border p-3 text-left transition-all"
            :class="project.id === store.activeProjectId ? 'border-cyan-500/50 bg-cyan-500/10 text-white' : 'border-white/[0.06] bg-zinc-900/40 text-zinc-400 hover:bg-zinc-900/60'"
            type="button"
            @click="selectProject(project.id)"
          >
            <span class="truncate text-sm font-medium">{{ project.name }}</span>
            <span class="shrink-0 font-mono text-[10px] opacity-70">{{ project.reid_model?.name || "模型待核验" }} · {{ project.project_type }}</span>
          </button>
          <div v-if="!store.activeProjects.length" class="py-4 text-center text-xs text-zinc-500">无项目</div>
        </div>

        <div class="border-t border-white/[0.06] pt-6">
          <p class="mb-4 text-xs uppercase tracking-widest text-zinc-500">新建项目</p>
          <input v-model="projectForm.name" class="field mb-3 w-full rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="项目名称" />
          <input v-model="projectForm.description" class="field mb-3 w-full rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="项目描述" />
          <div class="mb-4 grid grid-cols-2 gap-3">
            <select v-model="projectForm.projectType" class="field rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs">
              <option value="media">媒体项目</option>
              <option value="camera">相机项目</option>
            </select>
            <input v-model="projectForm.cameraSource" class="field rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="数据源" />
          </div>
      <ModelSelect v-model="reidModelId" scope="core" :open="open" />
          <button class="primary-button w-full justify-center rounded-xl py-3 text-sm" type="button" :disabled="!store.isAuthenticated || !reidModelId" @click="createProject">
            <FolderPlus :size="16" />
            <span>确认创建</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
