<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { Download, X } from "@lucide/vue";
import { downloadAsset } from "../lib/api/http";
import { useDialogFocus } from "../lib/dialogFocus";

export interface MediaPreviewState {
  open: boolean;
  title: string;
  type: "image" | "video";
  source: string;
  frameSource: string;
  downloadSource: string;
  fallbackSource: string;
  downloadName: string;
  seekSeconds: number;
  fallbackSeekSeconds: number;
  error?: string;
}

const props = withDefaults(defineProps<{
  preview: MediaPreviewState;
  variant?: "dark" | "light";
}>(), {
  variant: "dark",
});

const emit = defineEmits<{
  (event: "close"): void;
}>();

const video = ref<HTMLVideoElement | null>(null);
const dialog = ref<HTMLElement | null>(null);
const previewOpen = computed(() => props.preview.open);
const activeSource = ref("");
const activeSeekSeconds = ref(0);
const previewError = ref("");
const downloading = ref(false);
let seekTimer: number | undefined;
let seekAttempts = 0;
let seekComplete = false;

function clearSeekTimer(): void {
  if (seekTimer !== undefined) {
    window.clearTimeout(seekTimer);
    seekTimer = undefined;
  }
}

function scheduleSeek(): void {
  clearSeekTimer();
  if (!props.preview.open || props.preview.type !== "video" || activeSeekSeconds.value <= 0) return;
  seekTimer = window.setTimeout(seekVideo, 220);
}

function seekVideo(): void {
  const element = video.value;
  if (!element || seekComplete || element.readyState < 1 || element.seeking) return;
  const destination = Number.isFinite(element.duration)
    ? Math.min(activeSeekSeconds.value, Math.max(0, element.duration - 0.01))
    : activeSeekSeconds.value;
  try {
    element.currentTime = destination;
  } catch {
    // Browsers may reject an early seek until metadata is available.
  }
  seekAttempts += 1;
  if (!seekComplete && seekAttempts < 8) {
    scheduleSeek();
  }
}

function confirmSeek(): void {
  const element = video.value;
  if (!element || element.seeking) return;
  const destination = Number.isFinite(element.duration)
    ? Math.min(activeSeekSeconds.value, Math.max(0, element.duration - 0.01))
    : activeSeekSeconds.value;
  if (Math.abs(element.currentTime - destination) < 0.35) {
    seekComplete = true;
    clearSeekTimer();
  }
}

function close(): void {
  clearSeekTimer();
  video.value?.pause();
  emit("close");
}

function handleVideoError(): void {
  const fallback = props.preview.fallbackSource;
  if (fallback && fallback !== activeSource.value) {
    activeSource.value = fallback;
    activeSeekSeconds.value = Math.max(0, props.preview.fallbackSeekSeconds || 0);
    seekAttempts = 0;
    seekComplete = false;
    void nextTick(scheduleSeek);
    return;
  }
  previewError.value = "视频预览加载失败，下载文件仍可用。";
}

async function download(): Promise<void> {
  if (!props.preview.downloadSource || downloading.value) return;
  downloading.value = true;
  try {
    await downloadAsset(props.preview.downloadSource, props.preview.downloadName || "myreid-media");
  } catch (error) {
    previewError.value = error instanceof Error ? error.message : "下载失败";
  } finally {
    downloading.value = false;
  }
}

useDialogFocus(previewOpen, dialog, close);

watch(
  () => [props.preview.open, props.preview.source, props.preview.seekSeconds, props.preview.type] as const,
  async ([open, source, seekSeconds, type]) => {
    clearSeekTimer();
    if (!open) {
      video.value?.pause();
      return;
    }
    activeSource.value = source;
    activeSeekSeconds.value = Math.max(0, Number(seekSeconds || 0));
    previewError.value = props.preview.error || "";
    seekAttempts = 0;
    seekComplete = false;
    if (type === "video") {
      await nextTick();
      scheduleSeek();
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  clearSeekTimer();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="preview.open"
      class="preview-backdrop"
      :class="`preview-${variant}`"
      role="presentation"
      @click="close"
    >
      <section ref="dialog" class="preview-dialog" role="dialog" aria-modal="true" :aria-label="preview.title" tabindex="-1" @click.stop>
        <header class="preview-header">
          <div>
            <p>Media Preview</p>
            <h2>{{ preview.title }}</h2>
          </div>
          <button type="button" title="关闭" aria-label="关闭预览" @click="close"><X :size="18" /></button>
        </header>

        <div class="preview-layout">
          <div class="preview-media">
            <video
              v-if="preview.type === 'video'"
              :key="activeSource"
              ref="video"
              :src="activeSource"
              :poster="preview.frameSource"
              controls
              autoplay
              muted
              preload="metadata"
              playsinline
              @loadedmetadata="seekVideo"
              @loadeddata="seekVideo"
              @canplay="seekVideo"
              @seeked="confirmSeek"
              @error="handleVideoError"
            ></video>
            <img v-else :src="preview.source" alt="" />
          </div>

          <aside class="preview-details">
            <p v-if="previewError" class="preview-error">{{ previewError }}</p>
            <div class="preview-metric">
              <span>定位秒数</span>
              <strong>{{ activeSeekSeconds.toFixed(2) }}s</strong>
            </div>
            <img v-if="preview.frameSource" :src="preview.frameSource" alt="命中帧" />
            <button v-if="preview.downloadSource" type="button" :disabled="downloading" @click="download">
              <Download :size="16" />
              <span>{{ downloading ? "正在保存…" : (preview.type === "video" ? "下载视频" : "下载图片") }}</span>
            </button>
          </aside>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.preview-backdrop {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgb(0 0 0 / 72%);
  backdrop-filter: blur(12px);
}

.preview-dialog {
  width: min(1120px, 96vw);
  max-height: 92vh;
  overflow: hidden;
  border: 1px solid var(--preview-border);
  border-radius: 20px;
  background: var(--preview-surface);
  color: var(--preview-text);
  box-shadow: 0 28px 90px rgb(0 0 0 / 38%);
}

.preview-dark {
  --preview-surface: #111315;
  --preview-panel: #08090a;
  --preview-text: #f4f4f5;
  --preview-muted: #a1a1aa;
  --preview-border: rgb(255 255 255 / 10%);
  --preview-accent: #22d3ee;
}

.preview-light {
  --preview-surface: #fff;
  --preview-panel: #edf1f5;
  --preview-text: #0f172a;
  --preview-muted: #64748b;
  --preview-border: #d8e0e8;
  --preview-accent: #2563eb;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--preview-border);
}

.preview-header p {
  margin: 0 0 4px;
  color: var(--preview-muted);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.preview-header h2 {
  max-width: 80vw;
  margin: 0;
  overflow: hidden;
  font-size: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-header button {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid var(--preview-border);
  border-radius: 10px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.preview-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  min-height: 420px;
}

.preview-media {
  display: grid;
  min-width: 0;
  place-items: center;
  overflow: hidden;
  background: #000;
}

.preview-media video,
.preview-media img {
  width: 100%;
  max-height: 76vh;
  object-fit: contain;
}

.preview-details {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  padding: 18px;
  background: var(--preview-panel);
  border-left: 1px solid var(--preview-border);
}

.preview-details > img {
  width: 100%;
  border: 1px solid var(--preview-border);
  border-radius: 12px;
}

.preview-details > button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: auto;
  padding: 11px 14px;
  border-radius: 11px;
  background: var(--preview-accent);
  color: #071013;
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
  border: 0;
  cursor: pointer;
}

.preview-details > button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.preview-metric,
.preview-error {
  padding: 12px;
  border: 1px solid var(--preview-border);
  border-radius: 12px;
}

.preview-metric span {
  display: block;
  margin-bottom: 5px;
  color: var(--preview-muted);
  font-size: 11px;
}

.preview-metric strong {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.preview-error {
  margin: 0;
  color: #fb7185;
  font-size: 12px;
  line-height: 1.5;
}

@media (max-width: 760px) {
  .preview-backdrop { padding: 12px; }
  .preview-layout { grid-template-columns: 1fr; }
  .preview-details { border-top: 1px solid var(--preview-border); border-left: 0; }
}
</style>
