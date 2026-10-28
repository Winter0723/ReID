<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { projectsApi, type ReIDModel } from "../lib/api/projects";
import type { AuthScope } from "../lib/api/http";

const props = defineProps<{ scope: AuthScope; open: boolean; modelValue: string }>();
const emit = defineEmits<{ (event: "update:modelValue", value: string): void }>();
const models = ref<ReIDModel[]>([]);
const error = ref("");
const loading = ref(false);
let requestVersion = 0;
const selected = computed(() => models.value.find(model => model.id === props.modelValue));
async function refresh() {
  const version = ++requestVersion;
  const previous = props.modelValue;
  loading.value = true;
  error.value = "";
  emit("update:modelValue", "");
  try {
    const rows = await projectsApi.models(props.scope);
    if (version !== requestVersion) return;
    models.value = rows;
    emit("update:modelValue", models.value.find(model => model.id === previous && model.available)?.id
      || models.value.find(model => model.default && model.available)?.id
      || models.value.find(model => model.available)?.id || "");
  } catch (reason) {
    if (version !== requestVersion) return;
    models.value = [];
    error.value = reason instanceof Error ? reason.message : "无法加载模型列表";
  } finally { if (version === requestVersion) loading.value = false; }
}
watch(() => [props.open, props.scope], () => {
  if (props.open) void refresh();
  else { requestVersion++; loading.value = false; }
}, { immediate: true });
onBeforeUnmount(() => { requestVersion++; });
</script>

<template>
  <div class="model-picker">
    <label>项目模型
      <select aria-label="项目模型" :value="modelValue" :disabled="loading" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
        <option value="" disabled>{{ loading ? '正在读取模型…' : '请选择可用模型' }}</option>
        <option v-for="(model, index) in models" :key="`${model.id}-${index}`" :value="model.id" :disabled="!model.available">
          {{ model.name }} · {{ model.available ? `${model.dimension} 维` : model.unavailable_reason }}
        </option>
      </select>
    </label>
    <button type="button" :disabled="loading" @click="refresh">刷新模型列表</button>
    <p v-if="selected?.available">{{ selected.capabilities?.text ? '支持图片与文本检索' : '支持图片检索，不支持文本检索' }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!loading && !models.length" role="alert">模型目录中没有注册的模型，请检查权重和配套的 .model.json 文件。</p>
    <p>创建后不可更换模型；更换模型请新建项目。</p>
  </div>
</template>

<style scoped>
.model-picker { margin: 12px 0; font-size: 12px; color: #a1a1aa; }
label { display: grid; gap: 8px; }
select { width: 100%; padding: 10px; border: 1px solid #3f3f46; border-radius: 10px; background: #18181b; color: #fafafa; }
button { margin: 8px 0; color: #67e8f9; }
p { margin: 4px 0; line-height: 1.5; }
[role=alert] { color: #fda4af; }
</style>
