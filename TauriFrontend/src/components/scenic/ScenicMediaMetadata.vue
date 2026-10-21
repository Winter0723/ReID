<script setup lang="ts">
import { ref, watch } from "vue";
import { scenicEditingApi } from "../../lib/api/scenicEditing";
const props = defineProps<{ projectId: number; mediaId: number }>();
const location = ref("");
const recordedAt = ref("");
const busy = ref(false);
const message = ref("");
const open = ref(false);
watch(() => [props.projectId, props.mediaId], () => { open.value = false; message.value = ""; });
async function load(): Promise<void> {
  open.value = !open.value;
  if (!open.value) return;
  const id = props.mediaId;
  busy.value = true;
  try {
    const data = await scenicEditingApi.metadata(props.projectId, id);
    if (props.mediaId !== id) return;
    location.value = data.location || "";
    const date = data.recorded_at ? new Date(data.recorded_at) : null;
    recordedAt.value = date ? new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0,16) : "";
  } catch (cause) { message.value = cause instanceof Error ? cause.message : "无法读取素材信息"; }
  finally { busy.value = false; }
}
async function save(): Promise<void> {
  busy.value = true;
  try {
    await scenicEditingApi.saveMetadata(props.projectId, props.mediaId, { recorded_at: recordedAt.value ? new Date(recordedAt.value).toISOString() : null, location: location.value });
    message.value = "素材信息已保存，下次自动编排时生效。";
  } catch (cause) { message.value = cause instanceof Error ? cause.message : "素材信息保存失败"; }
  finally { busy.value = false; }
}
</script>
<template><div class="metadata"><button type="button" @click="load">{{ open ? '收起素材信息' : '补充拍摄时间与地点' }}</button><div v-if="open" class="metadata-fields"><label>拍摄时间（本地时区，可留空）<input v-model="recordedAt" type="datetime-local" aria-label="素材拍摄时间" :disabled="busy" /></label><label>拍摄地点<input v-model="location" maxlength="255" aria-label="素材拍摄地点" :disabled="busy" /></label><button type="button" :disabled="busy" @click="save">保存素材信息</button></div><p v-if="message" role="status">{{ message }}</p></div></template>
<style scoped>.metadata { margin-top: 15px; border-top: 1px solid #30483c; padding-top: 12px; }.metadata button { padding: 8px; border: 1px solid #3b5149; border-radius: 6px; color: #c7e7da; background: #1b2a26; font-size: 12px; }.metadata-fields { display: flex; align-items: end; flex-wrap: wrap; gap: 10px; margin-top: 12px; }.metadata label { display: grid; gap: 6px; color: #a5bbae; font-size: 11px; }.metadata input { padding: 8px; border: 1px solid #3b5149; border-radius: 6px; background: #101918; color: #ecfdf5; }.metadata p { color: #99c6af; font-size: 12px; }.metadata button:disabled { opacity: .45; }</style>
