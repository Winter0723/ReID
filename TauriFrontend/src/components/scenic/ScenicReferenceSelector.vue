<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { scenicApi } from "../../lib/api/scenic";
import { normalizeAssetUrl } from "../../lib/api/http";
import { useWorkbenchStore } from "../../stores/workbench";
const store = useWorkbenchStore();
type Box = [number, number, number, number];
const box = ref<Box | null>(null);
const origin = ref<[number, number] | null>(null);
const saving = ref(false);
const expanded = ref(false);
watch(() => store.selectedTourist?.id, () => { box.value = store.selectedTourist?.entry_target_box || null; expanded.value = false; }, { immediate: true });
const boxStyle = computed(() => box.value ? { left: `${box.value[0] * 100}%`, top: `${box.value[1] * 100}%`, width: `${(box.value[2] - box.value[0]) * 100}%`, height: `${(box.value[3] - box.value[1]) * 100}%` } : {});
function point(event: PointerEvent): [number, number] {
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
}
function begin(event: PointerEvent): void { if (saving.value) return; origin.value = point(event); (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); }
function move(event: PointerEvent): void {
  if (!origin.value) return;
  const end = point(event);
  box.value = [Math.min(origin.value[0], end[0]), Math.min(origin.value[1], end[1]), Math.max(origin.value[0], end[0]), Math.max(origin.value[1], end[1])];
}
async function save(clear = false): Promise<void> {
  const id = store.selectedTourist?.id;
  if (!id || saving.value) return;
  if (!clear && (!box.value || box.value[2] - box.value[0] < .02 || box.value[3] - box.value[1] < .02)) { store.error = "请拖动框选完整的目标游客。"; return; }
  saving.value = true;
  try { await scenicApi.setReference(id, clear ? null : box.value); if (id === store.selectedTourist?.id) { await store.selectTourist(id); box.value = clear ? null : box.value; } }
  catch (cause) { store.error = cause instanceof Error ? cause.message : "目标游客保存失败"; }
  finally { saving.value = false; }
}
</script>
<template>
  <section v-if="store.selectedTourist?.entry_image_url" class="reference-panel">
    <button type="button" class="reference-toggle" @click="expanded = !expanded">{{ expanded ? '收起' : '选择' }}登记照中的目标游客 <span>{{ store.selectedTourist.entry_target_box ? '已指定人物' : '多人照片可手动框选' }}</span></button>
    <div v-if="expanded" class="reference-body"><div class="reference-image" aria-label="目标游客框选区域" @pointerdown="begin" @pointermove="move" @pointerup="origin = null" @pointercancel="origin = null"><img :src="normalizeAssetUrl(store.selectedTourist.entry_image_url)" alt="拖动框选本次成片的目标游客" draggable="false" /><div v-if="box" class="target-box" :style="boxStyle"></div></div><div class="reference-copy"><p>拖动框选目标游客全身，排除同行者。保存后重新匹配，已有成片版本保持不变。</p><button type="button" :disabled="saving || !box" @click="save()">保存目标人物</button><button type="button" :disabled="saving" @click="save(true)">使用完整登记照</button></div></div>
  </section>
</template>
<style scoped>
.reference-panel { margin: 16px 0; border: 1px solid #29433b; border-radius: 8px; background: #101c17; }.reference-toggle { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; width: 100%; border: 0; padding: 12px; color: #d1fae5; background: transparent; text-align: left; font-size: 12px; }.reference-toggle span { color: #93b4a5; font-size: 11px; }.reference-body { display: flex; gap: 16px; align-items: start; padding: 0 12px 12px; }.reference-image { width: 240px; max-width: 100%; position: relative; flex-shrink: 0; cursor: crosshair; touch-action: none; user-select: none; }.reference-image img { display: block; width: 100%; }.target-box { position: absolute; border: 2px solid #5eead4; background: #14b8a625; pointer-events: none; }.reference-copy p { color: #a1b6aa; line-height: 1.7; font-size: 12px; margin: 0 0 12px; }.reference-copy button { padding: 8px 10px; border: 1px solid #336854; border-radius: 6px; background: #134e3d; color: #d1fae5; margin-right: 6px; margin-bottom: 6px; font-size: 12px; }.reference-copy button:disabled { opacity: .45; }
@media(max-width:600px) { .reference-body { flex-direction: column; } }
</style>
