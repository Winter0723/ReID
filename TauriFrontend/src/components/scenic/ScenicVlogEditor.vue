<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Film, LockKeyhole, Save, Sparkles } from "@lucide/vue";
import { scenicApi } from "../../lib/api/scenic";
import { scenicEditingApi, type DraftRecord, type EditingCatalog, type EditingClip, type VlogOrientation } from "../../lib/api/scenicEditing";
import { normalizeAssetUrl } from "../../lib/api/http";
import { resolveVideoPreviewSource } from "../../lib/videoPreview";
import { scenicTimelineDuration } from "../../lib/scenicDraftComparison";
import { useScenicStore } from "../../stores/scenic";
import { useWorkbenchStore } from "../../stores/workbench";
import ScenicMediaMetadata from "./ScenicMediaMetadata.vue";
import ScenicMusicPicker from "./ScenicMusicPicker.vue";
import ScenicDraftComparison from "./ScenicDraftComparison.vue";

const props = defineProps<{ catalog: EditingCatalog }>();
const store = useWorkbenchStore();
const cache = useScenicStore();
const sessionId = computed(() => store.selectedTourist?.id || 0);
const cacheKey = computed(() => `${store.scenicProjectId}:${sessionId.value}`);
const buffer = computed(() => cache.editingBuffers[cacheKey.value]);
const record = computed(() => buffer.value?.record);
const draft = computed(() => record.value?.data);
const dirty = computed(() => Boolean(buffer.value && JSON.stringify(buffer.value.record.data) !== buffer.value.saved));
const backup = computed(() => cache.editingBackups[cacheKey.value]);
const orientation = ref<VlogOrientation>("landscape");
const clips = computed(() => draft.value?.variants[orientation.value] || []);
const selectedClipId = ref("");
const clip = computed(() => clips.value.find((item) => item.id === selectedClipId.value) || clips.value[0]);
const duration = computed(() => scenicTimelineDuration(clips.value, props.catalog.templates.find((item) => item.id === draft.value?.template_id)?.transition_seconds));
const versions = ref<DraftRecord[]>([]);
const versionId = ref(0);
const comparisonOpen = ref(false);
const comparisonId = ref(0);
const comparisonRecord = computed(() => versions.value.find((item) => item.id === comparisonId.value));
const initialTemplate = ref("documentary");
const useTracking = ref(false);
const busy = ref("");
const error = ref("");
const notice = ref("");
const mediaSource = ref("");
const mediaError = ref("");
const videoElement = ref<HTMLVideoElement>();
const dragIndex = ref(-1);
const generation = ref(0);
const mediaDimensions = ref({ width: 0, height: 0 });
const focusMapSource = computed(() => normalizeAssetUrl(clip.value?.kind === "image" ? clip.value.original_media_url || clip.value.thumbnail_url || "" : clip.value?.thumbnail_url || ""));
const clipStyle = computed(() => {
  const current = clip.value;
  const width = mediaDimensions.value.width || current?.width || 1;
  const height = mediaDimensions.value.height || current?.height || 1;
  const ratio = orientation.value === "portrait" ? 9 / 16 : 16 / 9;
  const cropWidth = Math.min(width, height * ratio);
  const cropHeight = Math.min(height, width / ratio);
  const position = (focus: number, size: number, crop: number) => size === crop ? 50 : Math.max(0, Math.min(1, (focus * size - crop / 2) / (size - crop))) * 100;
  return { objectFit: current?.crop_mode === "fit" ? "contain" as const : "cover" as const, objectPosition: `${position(current?.focus_x ?? .5, width, cropWidth)}% ${position(current?.focus_y ?? .5, height, cropHeight)}%` };
});
watch(() => [record.value?.id, record.value?.data.template_id], () => { if (draft.value) initialTemplate.value = draft.value.template_id; });

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }
function acceptRecord(value: DraftRecord, key: string): void {
  cache.editingBuffers[key] = { record: clone(value), saved: JSON.stringify(value.data) };
}
function refreshAssetReferences(key: string, fresh: DraftRecord): void {
  const local = cache.editingBuffers[key];
  const sources = new Map(fresh.data.sources.map((item) => [item.id, item]));
  const update = (data: DraftRecord["data"]) => {
    for (const item of [...data.sources, ...data.variants.landscape, ...data.variants.portrait]) {
      const source = sources.get(item.id);
      if (source) { item.thumbnail_url = source.thumbnail_url; item.original_media_url = source.original_media_url; }
    }
  };
  const saved = JSON.parse(local.saved) as DraftRecord["data"];
  update(saved);
  update(local.record.data);
  local.saved = JSON.stringify(saved);
}
async function loadVersions(): Promise<void> {
  const id = sessionId.value;
  const key = cacheKey.value;
  if (!id) { versions.value = []; return; }
  try {
    const response = await scenicEditingApi.drafts(id);
    if (key !== cacheKey.value) return;
    versions.value = response.drafts;
    versionId.value = response.drafts[0]?.id || 0;
    if (!response.drafts.some((item) => item.id === comparisonId.value)) comparisonId.value = response.drafts[0]?.id || 0;
    if (!cache.editingBuffers[key] && response.drafts[0]) acceptRecord(response.drafts[0], key);
    else if (cache.editingBuffers[key]) {
      const fresh = response.drafts.find((item) => item.id === cache.editingBuffers[key].record.id);
      if (fresh) refreshAssetReferences(key, fresh);
    }
  } catch (cause) { if (key === cacheKey.value) error.value = cause instanceof Error ? cause.message : "读取草稿失败"; }
}
watch(cacheKey, () => { error.value = ""; notice.value = ""; selectedClipId.value = ""; versions.value = []; comparisonOpen.value = false; comparisonId.value = 0; void loadVersions(); }, { immediate: true });

async function saveRecord(): Promise<DraftRecord | undefined> {
  if (!record.value) return;
  const key = cacheKey.value;
  const response = await scenicEditingApi.save(sessionId.value, clone(record.value));
  acceptRecord(response, key);
  return response;
}
async function action(label: string, task: () => Promise<void>): Promise<void> {
  if (busy.value) return;
  busy.value = label;
  error.value = "";
  notice.value = "";
  try { await task(); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "操作失败，请重试"; }
  finally { busy.value = ""; }
}
function save(): void {
  void action("正在保存草稿", async () => { await saveRecord(); await loadVersions(); notice.value = "草稿已保存，提交后该版本会被冻结。"; });
}
function autoDraft(): void {
  void action("分析素材并编排镜头", async () => {
    const id = sessionId.value;
    const key = cacheKey.value;
    const requestedTemplate = initialTemplate.value;
    if (!id) return;
    if (dirty.value) await saveRecord();
    const current = cache.editingBuffers[key]?.record;
    const data = current?.data;
    const template = props.catalog.templates.find((item) => item.id === requestedTemplate) || props.catalog.templates[0];
    const result = await scenicEditingApi.auto(id, {
      template_id: template.id, title: data?.title ?? "我的游览记忆", brand: data?.brand ?? "",
      music_id: data?.template_id === template.id ? data.music_id : template.music_id, music_volume: data?.music_volume ?? .25,
      original_volume: data?.original_volume ?? 1, landscape_seconds: data?.landscape_seconds ?? 75,
      portrait_seconds: data?.portrait_seconds ?? 45, use_tracking: useTracking.value,
      ...(current ? { previous_draft_id: current.id } : {}),
    });
    acceptRecord(result, key);
    if (key === cacheKey.value) { await loadVersions(); notice.value = "已编排横竖两版，可逐镜头微调。"; }
  });
}
function openVersion(): void {
  if (dirty.value) return;
  const chosen = versions.value.find((item) => item.id === versionId.value);
  if (chosen) { acceptRecord(chosen, cacheKey.value); selectedClipId.value = ""; }
}
function reloadLatest(): void {
  void action("保留本地修改并读取最新草稿", async () => {
    const current = record.value;
    if (!current) return;
    const key = cacheKey.value;
    cache.editingBackups[key] = clone(current);
    const response = await scenicEditingApi.drafts(sessionId.value);
    const latest = response.drafts.find((item) => item.id === current.id);
    if (!latest) throw new Error("服务器上未找到当前草稿，本地内容仍保留。");
    acceptRecord(latest, key);
    if (key === cacheKey.value) { versions.value = response.drafts; notice.value = "已读取最新修订。本地备份可恢复，恢复后可重新保存。"; }
  });
}
function restoreBackup(): void {
  if (!backup.value || !record.value) return;
  record.value.data = clone(backup.value.data);
  notice.value = "已恢复本地备份。检查后保存，将使用当前服务器修订提交。";
}
function submit(): void {
  void action("冻结版本并提交双版成片", async () => {
    const id = sessionId.value;
    const saved = dirty.value ? await saveRecord() : record.value;
    if (!saved) return;
    await scenicApi.generateVlog(id, { draft_id: saved.id, revision: saved.revision });
    if (id === sessionId.value) await store.selectTourist(id);
    notice.value = "横版和竖版已分别排队。可继续编辑下一版本。";
  });
}
function changeTemplate(): void {
  notice.value = "已选择新模板。点击重新自动编排以应用镜头节奏，已锁定的镜头将保留。";
}
function move(from: number, to: number): void {
  if (busy.value || !draft.value || from < 0 || to < 0 || to >= clips.value.length) return;
  const [item] = clips.value.splice(from, 1);
  if (item) clips.value.splice(to, 0, item);
}
function linkedClips(id: string): EditingClip[] {
  return Object.values(draft.value?.variants || {}).flat().filter((item) => item.id === id);
}
function setClipIncluded(id: string, event: Event): void {
  const matches = linkedClips(id);
  if (busy.value || matches.some((item) => item.locked)) return;
  const included = (event.target as HTMLInputElement).checked;
  for (const item of matches) item.included = included;
}
function addSource(source: EditingClip): void {
  if (!draft.value) return;
  const existing = clips.value.find((item) => item.id === source.id);
  if (existing) { existing.included = true; selectedClipId.value = existing.id; return; }
  clips.value.push(clone({ ...source, included: true }));
  selectedClipId.value = source.id;
}
function setFocus(event: MouseEvent): void {
  if (!clip.value || busy.value) return;
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  clip.value.focus_x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  clip.value.focus_y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  clip.value.crop_mode = "focus";
}
function checkPlayhead(): void {
  if (!videoElement.value || !clip.value) return;
  if (videoElement.value.currentTime >= clip.value.end_seconds) videoElement.value.pause();
}
function seekStart(): void {
  if (videoElement.value && clip.value) {
    videoElement.value.currentTime = clip.value.start_seconds;
    mediaDimensions.value = { width: videoElement.value.videoWidth, height: videoElement.value.videoHeight };
  }
}
function imageLoaded(event: Event): void {
  const image = event.currentTarget as HTMLImageElement;
  mediaDimensions.value = { width: image.naturalWidth, height: image.naturalHeight };
}
watch(() => [clip.value?.id, clip.value?.original_media_url, sessionId.value], async () => {
  const request = ++generation.value;
  mediaSource.value = "";
  mediaError.value = "";
  mediaDimensions.value = { width: 0, height: 0 };
  const current = clip.value;
  if (!current) return;
  if (current.kind === "image") { mediaSource.value = normalizeAssetUrl(current.original_media_url || current.thumbnail_url || ""); return; }
  try {
    const source = await resolveVideoPreviewSource({ scope: "scenic", projectId: store.scenicProjectId, mediaId: current.media_id, full: true, fallback: current.original_media_url || "", startSeconds: 0 });
    if (request === generation.value) mediaSource.value = normalizeAssetUrl(source.source);
  } catch (cause) { if (request === generation.value) mediaError.value = cause instanceof Error ? cause.message : "原片暂时无法预览"; }
}, { immediate: true });
</script>

<template>
  <section class="editor" aria-label="Vlog 剪辑工作台">
    <header class="editor-head">
      <div><span class="eyebrow">游客 VLOG</span><h3>把游览时刻，剪成一份记忆</h3><p>自动精选真实素材，横竖两版独立编排。</p></div>
      <span class="save-state" aria-live="polite">{{ busy || (dirty ? "有未保存修改 · 切换游客仍会保留" : record ? `草稿 #${record.id} · 修订 ${record.revision}` : "尚未创建草稿") }}</span>
    </header>
    <div v-if="error" class="error" role="alert">{{ error }}<button v-if="record" type="button" :disabled="Boolean(busy)" @click="reloadLatest">备份本地修改并读取最新</button></div>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <fieldset :disabled="Boolean(busy)" class="editor-fields">
      <div class="editor-toolbar">
        <button v-if="backup" type="button" :disabled="dirty" @click="restoreBackup">恢复本地备份</button>
        <label v-if="!draft">剪辑风格<select v-model="initialTemplate" aria-label="剪辑风格"><option v-for="item in catalog.templates" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <button class="primary" type="button" :disabled="!sessionId" @click="autoDraft"><Sparkles :size="16" />{{ draft ? "重新自动编排" : "生成自动草稿" }}</button>
        <label class="check"><input v-model="useTracking" type="checkbox" />本地人物分析</label>
        <button type="button" :disabled="!dirty" @click="save"><Save :size="15" />保存草稿</button>
        <button class="primary" type="button" :disabled="!draft || initialTemplate !== draft.template_id" @click="submit"><Film :size="16" />生成横竖双版</button>
      </div>
      <div v-if="versions.length" class="version-bar"><label>已有草稿<select v-model.number="versionId" aria-label="已有草稿"><option v-for="item in versions" :key="item.id" :value="item.id">#{{ item.id }} · {{ item.data.title || '游览记忆' }} · 修订 {{ item.revision }}</option></select></label><button type="button" :disabled="dirty" @click="openVersion">打开草稿</button><button v-if="record" type="button" :aria-expanded="comparisonOpen" @click="comparisonOpen = !comparisonOpen">{{ comparisonOpen ? '收起比较' : '比较草稿' }}</button><small v-if="dirty">先保存当前修改，再打开其他版本。</small></div>
      <section v-if="comparisonOpen && record" class="comparison-panel" aria-label="草稿版本比较">
        <label class="comparison-selector">比较基准草稿<select v-model.number="comparisonId" aria-label="比较基准草稿"><option v-for="item in versions" :key="item.id" :value="item.id">#{{ item.id }} · {{ item.data.title || '游览记忆' }} · 修订 {{ item.revision }}</option></select></label>
        <ScenicDraftComparison v-if="comparisonRecord" :current="record" :saved="comparisonRecord" :catalog="catalog" :dirty="dirty" />
        <p v-else class="preview-note">请选择一个已保存的草稿进行比较。</p>
      </section>
      <template v-if="draft">
        <div class="settings-grid">
          <label>剪辑风格<select v-model="initialTemplate" aria-label="剪辑风格" @change="changeTemplate"><option v-for="item in catalog.templates" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <label>影片标题<input v-model="draft.title" maxlength="80" aria-label="影片标题" /></label>
          <label>景区署名<input v-model="draft.brand" maxlength="40" placeholder="景区名称（可选）" aria-label="景区署名" /></label>
          <label>横版目标（秒）<input v-model.number="draft.landscape_seconds" type="number" min="60" max="90" aria-label="横版目标时长" /></label>
          <label>竖版目标（秒）<input v-model.number="draft.portrait_seconds" type="number" min="30" max="60" aria-label="竖版目标时长" /></label>
          <ScenicMusicPicker v-model:music-id="draft.music_id" :music="catalog.music" :volume="draft.music_volume" />
          <label>音乐音量 {{ Math.round(draft.music_volume * 100) }}%<input v-model.number="draft.music_volume" type="range" min="0" max="1" step="0.05" aria-label="音乐音量" /></label>
          <label>原声音量 {{ Math.round(draft.original_volume * 100) }}%<input v-model.number="draft.original_volume" type="range" min="0" max="1" step="0.05" aria-label="原声音量" /></label>
        </div>
        <div v-if="draft.warnings.length" class="warnings" role="status"><p v-for="warning in draft.warnings" :key="warning">{{ warning }}</p></div>
        <div class="workspace-grid">
          <aside class="source-panel"><h4>可用素材 <span>{{ draft.sources.length }}</span></h4><div class="source-list"><button v-for="source in draft.sources" :key="source.id" class="source-item" type="button" @click="addSource(source)"><img v-if="source.thumbnail_url" :src="normalizeAssetUrl(source.thumbnail_url)" alt="" /><span>{{ source.camera_name || '游览时刻' }}<small>{{ source.kind === 'image' ? '照片' : '视频' }} · 加入当前画幅</small></span></button></div></aside>
          <div class="preview-panel"><div class="orientation-tabs"><button type="button" :class="{ active: orientation === 'landscape' }" @click="orientation = 'landscape'">横版 16:9</button><button type="button" :class="{ active: orientation === 'portrait' }" @click="orientation = 'portrait'">竖版 9:16</button></div><div class="preview-stage" :class="orientation"><video v-if="mediaSource && clip?.kind === 'video'" ref="videoElement" :src="mediaSource" :style="clipStyle" controls :muted="!clip.keep_audio" @loadedmetadata="seekStart" @timeupdate="checkPlayhead" /><img v-else-if="mediaSource" :src="mediaSource" :style="clipStyle" alt="当前镜头构图" @load="imageLoaded" /><span v-else>{{ mediaError || '选择镜头以预览' }}</span><div v-if="clip" class="caption-preview">{{ draft.title }}<small>{{ clip.camera_name }}</small></div></div><p class="preview-note">单镜头构图示意；完整配乐、字幕与跟随效果请查看生成后的工作预览。</p><strong class="timeline-summary">{{ clips.filter(item => item.included).length }} 个镜头 · 预计成片 {{ duration.toFixed(1) }} 秒</strong></div>
          <aside v-if="clip" class="clip-inspector"><h4>镜头微调</h4><label class="check"><input v-model="clip.included" :disabled="clip.locked" type="checkbox" />用于此画幅</label><label class="check"><input v-model="clip.locked" type="checkbox" /><LockKeyhole :size="13" />锁定，自动编排保留</label><label class="check"><input v-model="clip.keep_audio" :disabled="clip.kind === 'image'" type="checkbox" />保留原声</label><div class="trim-row"><label>入点（秒）<input v-model.number="clip.start_seconds" :disabled="clip.kind === 'image'" type="number" min="0" :max="clip.end_seconds - .1" step="0.1" aria-label="镜头入点" @change="seekStart" /></label><label>出点（秒）<input v-model.number="clip.end_seconds" type="number" :min="clip.start_seconds + .1" step="0.1" aria-label="镜头出点" /></label></div><label>构图方式<select v-model="clip.crop_mode" aria-label="构图方式"><option value="fit">完整画面填充</option><option value="focus">手动焦点裁切</option><option value="track" :disabled="!clip.trajectory">人物跟随（有轨迹时）</option></select></label><button v-if="focusMapSource" class="focus-map" type="button" aria-label="在原图选择焦点" @click="setFocus"><img :src="focusMapSource" alt="在原图点击设置焦点" /><span :style="{ left: `${clip.focus_x * 100}%`, top: `${clip.focus_y * 100}%` }">+</span></button><label>水平焦点<input v-model.number="clip.focus_x" type="range" min="0" max="1" step="0.01" aria-label="水平焦点" /></label><label>垂直焦点<input v-model.number="clip.focus_y" type="range" min="0" max="1" step="0.01" aria-label="垂直焦点" /></label><button type="button" :class="{ active: draft.cover_clip_id === clip.id }" @click="draft.cover_clip_id = clip.id">{{ draft.cover_clip_id === clip.id ? '已用作封面' : '用作封面' }}</button></aside>
        </div>
        <ScenicMediaMetadata v-if="clip" :project-id="store.scenicProjectId" :media-id="clip.media_id" />
        <p class="preview-note">下方“包含”同步选择横竖两版中的同一片段。单版选择可在“镜头微调”中调整；顺序、入出点和构图仍按画幅独立编辑。</p>
        <div class="timeline" aria-label="镜头时间线"><article v-for="(item, index) in clips" :key="item.id" class="timeline-item" :class="{ selected: clip?.id === item.id, excluded: !item.included }" draggable="true" @dragstart="dragIndex = index" @dragover.prevent @drop.prevent="move(dragIndex, index)"><button class="timeline-select" type="button" :aria-label="`编辑镜头 ${index + 1}`" @click="selectedClipId = item.id"><img v-if="item.thumbnail_url" :src="normalizeAssetUrl(item.thumbnail_url)" alt="" /><span>{{ index + 1 }} · {{ item.camera_name || '游览时刻' }}<small>{{ (item.end_seconds - item.start_seconds).toFixed(1) }}s {{ item.locked ? '· 已锁定' : '' }} {{ !item.included ? '· 已排除' : '' }}</small></span></button><div class="timeline-actions"><button type="button" :disabled="index === 0" :aria-label="`前移镜头 ${index + 1}`" @click="move(index, index - 1)">←</button><button type="button" :disabled="index === clips.length - 1" :aria-label="`后移镜头 ${index + 1}`" @click="move(index, index + 1)">→</button><label class="check" :title="linkedClips(item.id).some(clip => clip.locked) ? '此镜头已锁定，请先解除横竖版中的锁定' : '同步设置横竖两版是否包含此镜头'"><input :checked="linkedClips(item.id).some(clip => clip.included)" :indeterminate="linkedClips(item.id).some(clip => clip.included) && linkedClips(item.id).some(clip => !clip.included)" :disabled="linkedClips(item.id).some(clip => clip.locked)" type="checkbox" :aria-label="`包含镜头 ${index + 1}`" @change="setClipIncluded(item.id, $event)" />包含</label></div></article></div>
      </template>
      <div v-else class="editor-empty"><Sparkles :size="28" /><h4>从已复核的游览素材开始</h4><p>自动去重、选择有效镜头并生成两份时间线。素材不足时生成更短的回忆。</p></div>
    </fieldset>
  </section>
</template>

<style scoped>
.editor { margin-top: 18px; border: 1px solid #25433f; border-radius: 12px; padding: 18px; background: #101b1b; color: #e4e4e7; }
.version-bar>label { flex: 0 1 360px; }.comparison-panel { min-width: 0; margin-top: 14px; padding: 14px; border: 1px solid #34584b; border-radius: 8px; background: #101f1a; }.comparison-selector { max-width: 420px; margin-bottom: 14px; }
.editor-head { display: flex; justify-content: space-between; gap: 16px; align-items: center; }.editor h3 { margin: 5px 0; font-size: 19px; color: #f0fdfa; }.editor h4 { margin: 0 0 12px; font-size: 13px; }.editor-head p,.preview-note { margin: 6px 0; font-size: 12px; color: #a3b8b4; }.eyebrow { letter-spacing: .15em; font-size: 10px; color: #5eead4; }.save-state { font-size: 11px; color: #b5cbc4; max-width: 250px; }.editor-fields { padding: 0; margin: 0; min-width: 0; border: 0; }.editor-toolbar,.version-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin-top: 16px; }.version-bar { padding-top: 12px; border-top: 1px solid #29403a; }.editor label { display: grid; gap: 5px; font-size: 11px; color: #b8cbc5; min-width: 0; }.editor input:not([type=checkbox]):not([type=range]),.editor select { width: 100%; min-width: 0; border: 1px solid #344a45; border-radius: 6px; padding: 8px; color: #ecfdf5; background: #101918; font-size: 12px; }.editor input[type=range] { width: 100%; accent-color: #2dd4bf; }.editor .check { display: flex; gap: 6px; align-items: center; }.check input { accent-color: #14b8a6; }.editor button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 8px 11px; border: 1px solid #3b5149; border-radius: 6px; background: #1b2a26; color: #dbf4e9; font-size: 12px; cursor: pointer; }.editor button.primary { background: #0f766e; border-color: #14b8a6; color: #fff; }.editor button:disabled { cursor: not-allowed; opacity: .45; }.editor button.active { border-color: #5eead4; background: #134e4a; }.settings-grid { display: grid; align-items: start; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 13px; margin: 18px 0; }.workspace-grid { display: grid; grid-template-columns: 135px minmax(220px,1fr) 215px; gap: 14px; }.source-panel,.clip-inspector { padding: 12px; background: #0b1413; border: 1px solid #273e36; border-radius: 8px; min-width: 0; }.source-panel h4 span { color: #72b9a9; }.source-list { display: grid; gap: 8px; max-height: 390px; overflow: auto; }.editor .source-item { flex-direction: column; padding: 6px; justify-content: start; text-align: left; }.source-item img { width: 100%; height: 75px; object-fit: cover; border-radius: 4px; }.source-item span,.source-item small { display: block; width: 100%; }.source-item small { color: #8da99e; font-size: 10px; margin-top: 3px; }.preview-panel { min-width: 0; display: flex; align-items: center; flex-direction: column; }.orientation-tabs { display: flex; gap: 5px; margin-bottom: 12px; }.preview-stage { position: relative; display: flex; justify-content: center; align-items: center; overflow: hidden; background: #050a09; border: 1px solid #304239; border-radius: 6px; width: 100%; aspect-ratio: 16/9; }.preview-stage.portrait { aspect-ratio: 9/16; width: min(100%,200px); }.preview-stage video,.preview-stage>img { width: 100%; height: 100%; }.preview-stage>span { color: #90a59b; padding: 18px; font-size: 12px; text-align: center; }.caption-preview { position: absolute; bottom: 42px; left: 15px; right: 15px; font-size: 14px; color: white; text-shadow: 0 1px 5px #000; pointer-events: none; }.caption-preview small { display: block; font-size: 10px; }.preview-note { max-width: 390px; line-height: 1.6; }.timeline-summary { margin-top: 10px; color: #6ee7c4; font-size: 12px; }.clip-inspector { display: flex; flex-direction: column; gap: 10px; }.clip-inspector h4 { margin: 0; }.trim-row { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }.editor .focus-map { padding: 0; display: block; position: relative; overflow: hidden; }.focus-map img { width: 100%; height: auto; display: block; }.focus-map span { position: absolute; transform: translate(-50%,-50%); color: #fef08a; font-size: 24px; text-shadow: 0 0 3px #000; }.timeline { display: flex; gap: 10px; overflow-x: auto; padding: 15px 0 5px; }.timeline-item { border: 1px solid #354840; border-radius: 8px; width: 168px; flex: 0 0 168px; overflow: hidden; background: #0d1815; }.timeline-item.selected { border-color: #5eead4; box-shadow: 0 0 0 1px #5eead4; }.timeline-item.excluded { opacity: .55; }.editor .timeline-select { width: 100%; flex-direction: column; border: 0; border-radius: 0; padding: 6px; align-items: start; text-align: left; background: transparent; }.timeline-select img { width: 100%; height: 65px; object-fit: cover; border-radius: 4px; }.timeline-select span { font-size: 11px; }.timeline-select small { display: block; color: #8ba898; margin-top: 3px; }.timeline-actions { display: flex; gap: 4px; padding: 5px; }.editor .timeline-actions button { padding: 4px 8px; }.timeline-actions .check { font-size: 10px; }.error,.notice,.warnings { padding: 10px 12px; border-radius: 6px; font-size: 12px; line-height: 1.6; }.error { background: #4c1724; color: #fecdd3; }.notice { background: #133c33; color: #a7f3d0; }.warnings { margin-bottom: 14px; background: #3b321a; color: #fef3c7; }.warnings p { margin: 0; }.editor-empty { padding: 42px 20px; text-align: center; color: #9db6aa; }.editor-empty h4 { margin: 12px 0; color: #cde8dd; }.editor-empty p { margin: 0; font-size: 12px; }.version-bar small { font-size: 11px; color: #fcd34d; }
@media(max-width:1100px) { .workspace-grid { grid-template-columns: 135px minmax(190px,1fr); }.clip-inspector { grid-column: 1/-1; display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); align-items: start; }.clip-inspector h4 { grid-column: 1/-1; } }
@media(max-width:700px) { .editor { padding: 12px; }.editor-head { align-items: start; flex-direction: column; }.settings-grid,.clip-inspector { grid-template-columns: repeat(2,minmax(0,1fr)); }.workspace-grid { grid-template-columns: 1fr; }.source-panel { display: none; }.clip-inspector { grid-column: auto; }.editor h3 { font-size: 17px; } }
</style>
