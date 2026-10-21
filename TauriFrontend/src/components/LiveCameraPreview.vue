<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { apiRequest, normalizeAssetUrl } from "../lib/api/http";

const props = defineProps<{ src: string; alt: string; streamEndpoint?: string; authScope?: "core" | "scenic" }>();
const streamUrl = ref("");
const streamReady = ref(false);
const streamImage = ref<HTMLImageElement | null>(null);
let streamGeneration = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
let streamRequest: AbortController | undefined;

async function connectStream(): Promise<void> {
  const generation = ++streamGeneration;
  clearTimeout(reconnectTimer);
  streamRequest?.abort();
  if (!props.streamEndpoint || disposed) return;
  streamRequest = new AbortController();
  try {
    const result = await apiRequest<{ url: string; expires_in: number }>(props.streamEndpoint, {
      authScope: props.authScope || "core", signal: streamRequest.signal, timeoutMs: 10000,
    });
    if (disposed || generation !== streamGeneration) return;
    if (!result.url) throw new Error("Missing preview stream URL");
    streamUrl.value = normalizeAssetUrl(result.url);
    reconnectTimer = setTimeout(() => void connectStream(), Math.max(1000, (result.expires_in - 5) * 1000));
  } catch {
    if (disposed || generation !== streamGeneration) return;
    streamFailed();
  }
}

function streamFailed(): void {
  streamUrl.value = "";
  streamReady.value = false;
  loadLatestFrame();
  clearTimeout(reconnectTimer);
  if (!disposed && props.streamEndpoint) reconnectTimer = setTimeout(() => void connectStream(), 2000);
}
const frames = ref(["", ""]);
const visibleIndex = ref(-1);
let pending: { index: number; source: string } | null = null;
let disposed = false;

watch(() => [props.streamEndpoint, props.authScope], () => {
  streamUrl.value = "";
  streamReady.value = false;
  void connectStream();
}, { immediate: true });

function loadLatestFrame(): void {
  if (disposed || streamReady.value || pending || !props.src || frames.value[visibleIndex.value] === props.src) return;
  const index = visibleIndex.value === 0 ? 1 : 0;
  pending = { index, source: props.src };
  frames.value[index] = props.src;
}

function finishFrame(request: NonNullable<typeof pending>, decoded: boolean): void {
  if (disposed || pending !== request) return;
  if (decoded) visibleIndex.value = request.index;
  pending = null;
  // Finish the current download, then skip directly to the newest frame.
  // Cancelling on every polling tick would starve slower connections.
  if (props.src !== request.source) loadLatestFrame();
}

async function frameLoaded(index: number, event: Event): Promise<void> {
  const request = pending;
  if (!request || request.index !== index) return;
  const image = event.currentTarget as HTMLImageElement;
  try {
    if (image.decode) await image.decode();
    finishFrame(request, true);
  } catch {
    finishFrame(request, false);
  }
}

function frameFailed(index: number): void {
  if (pending?.index === index) finishFrame(pending, false);
}

watch(() => props.src, (source) => {
  if (!source) {
    pending = null;
    visibleIndex.value = -1;
    frames.value = ["", ""];
  } else {
    loadLatestFrame();
  }
}, { immediate: true });

onBeforeUnmount(() => {
  disposed = true;
  streamGeneration++;
  streamRequest?.abort();
  clearTimeout(reconnectTimer);
  streamImage.value?.removeAttribute("src");
  streamUrl.value = "";
  pending = null;
});
</script>

<template>
  <div class="live-camera-preview">
    <img v-if="streamUrl" ref="streamImage" :src="streamUrl" :alt="streamReady ? alt : ''" class="stream" @load="streamReady = true" @error="streamFailed" />
    <img
      v-for="(source, index) in frames"
      :key="index"
      :src="source || undefined"
      :alt="!streamReady && index === visibleIndex ? alt : ''"
      :aria-hidden="streamReady || index !== visibleIndex"
      :style="{ visibility: !streamReady && index === visibleIndex ? 'visible' : 'hidden' }"
      @load="frameLoaded(index, $event)"
      @error="frameFailed(index)"
    />
  </div>
</template>

<style scoped>
img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.stream { z-index: 1; }
</style>
