<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { EditingCatalog } from "../../lib/api/scenicEditing";
import { normalizeAssetUrl } from "../../lib/api/http";

const props = defineProps<{ music: EditingCatalog["music"]; musicId: string; volume: number }>();
const emit = defineEmits<{ "update:musicId": [value: string] }>();
const selected = computed(() => props.musicId === "none" ? undefined : props.music.find((item) => item.id === props.musicId));
const preview = computed(() => normalizeAssetUrl(selected.value?.preview_url || ""));
const audio = ref<HTMLAudioElement>();
const failed = ref(false);
const duration = computed(() => {
  const seconds = selected.value?.duration_seconds;
  if (!seconds || !Number.isFinite(seconds)) return "";
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
});

watch(() => [props.musicId, preview.value], () => {
  audio.value?.pause();
  failed.value = false;
});
watch([audio, () => props.volume], () => {
  if (audio.value) audio.value.volume = Number.isFinite(props.volume) ? Math.max(0, Math.min(1, props.volume)) : .25;
}, { flush: "post" });
onBeforeUnmount(() => audio.value?.pause());
</script>

<template>
  <div class="music-picker">
    <label>配乐
      <select :value="musicId || 'none'" aria-label="配乐" @change="emit('update:musicId', ($event.target as HTMLSelectElement).value)">
        <option value="none">关闭配乐</option>
        <option v-for="item in music" :key="item.id" :value="item.id">{{ item.name }}</option>
      </select>
    </label>
    <div v-if="selected" class="music-details" aria-label="配乐信息">
      <p v-if="selected.style || duration" class="music-style">{{ [selected.style, duration].filter(Boolean).join(' · ') }}</p>
      <p v-if="selected.description">{{ selected.description }}</p>
      <p v-if="selected.source || selected.author">
        <a v-if="selected.source_url" :href="selected.source_url" target="_blank" rel="noopener noreferrer">{{ selected.source || '曲目来源' }}</a>
        <span v-else>{{ selected.source }}</span><span v-if="selected.author"> · {{ selected.author }}</span>
      </p>
      <p v-if="selected.license || selected.usage">
        <a v-if="selected.license_url" :href="selected.license_url" target="_blank" rel="noopener noreferrer">{{ selected.license || '授权说明' }}</a>
        <span v-else>{{ selected.license }}</span><span v-if="selected.usage">{{ selected.license ? ' · ' : '' }}{{ selected.usage }}</span>
      </p>
      <audio v-if="preview" :key="`${musicId}:${preview}`" ref="audio" :src="preview" aria-label="配乐试听" controls preload="none" @error="failed = true" @canplay="failed = false" />
      <p v-if="failed" class="music-error" role="status">试听暂不可用，请重新打开剪辑页面后重试。</p>
    </div>
  </div>
</template>

<style scoped>
.music-picker { min-width: 0; }
.music-picker label { display: grid; gap: 5px; font-size: 11px; color: #b8cbc5; }
.music-picker select { width: 100%; min-width: 0; border: 1px solid #344a45; border-radius: 6px; padding: 8px; color: #ecfdf5; background: #101918; font-size: 12px; }
.music-details { display: grid; gap: 5px; margin-top: 7px; font-size: 11px; line-height: 1.5; color: #9eb6ad; overflow-wrap: anywhere; }
.music-details p { margin: 0; }
.music-style { color: #c2d8cf; }
.music-details a { color: #7dddc3; text-decoration: underline; text-underline-offset: 2px; }
.music-details a:hover { color: #ccfbf1; }
.music-details audio { display: block; width: 100%; height: 34px; margin-top: 3px; }
.music-details .music-error { color: #fcd34d; }
</style>
