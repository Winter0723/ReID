<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { showConfirmDialog } from "vant";
import {
  api,
  type Visit,
  type VisitorEditorData,
  type StoryDraft,
  type StoryClip,
  type Orientation,
  type StoryConfig,
} from "../../shared/api";
const props = defineProps<{ visitId: number }>();
const emit = defineEmits<{ updated: [visit: Visit]; close: [] }>();
const editor = ref<VisitorEditorData>(),
  draft = ref<StoryDraft | null>(null),
  error = ref(""),
  busy = ref(false);
const selecting = ref(true),
  selected = ref<number[]>([]),
  orientation = ref<Orientation>("landscape");
const preview = ref<{ source: string; clip?: StoryClip }>(),
  player = ref<HTMLVideoElement>();
const previewPanel = ref<HTMLDivElement>();
async function showPreview(source: string, clip?: StoryClip) {
  preview.value = { source, clip };
  await nextTick();
  previewPanel.value?.scrollIntoView({ block: "start", behavior: "smooth" });
}
const config = ref<StoryConfig>({
  template_id: "documentary",
  title: "我的游览回忆",
  brand: "",
  music_id: "auto",
  music_volume: 0.25,
  original_volume: 1,
  landscape_seconds: 75,
  portrait_seconds: 45,
  use_tracking: false,
});
const clips = computed(
  () => draft.value?.data.variants[orientation.value] || [],
);
const duration = computed(() =>
  clips.value
    .filter((clip) => clip.included)
    .reduce((sum, clip) => sum + clip.end_seconds - clip.start_seconds, 0),
);
const base = computed(() => `/visits/${props.visitId}`);
function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}
function configFrom(data: StoryConfig): StoryConfig {
  const {
    template_id,
    title,
    brand,
    music_id,
    music_volume,
    original_volume,
    landscape_seconds,
    portrait_seconds,
    use_tracking,
  } = data;
  return {
    template_id,
    title,
    brand,
    music_id,
    music_volume,
    original_volume,
    landscape_seconds,
    portrait_seconds,
    use_tracking,
  };
}
async function load() {
  busy.value = true;
  try {
    editor.value = await api<VisitorEditorData>(base.value + "/editor");
    selected.value = editor.value.segments
      .filter((segment) => segment.included)
      .map((segment) => segment.id);
    draft.value = copy(editor.value.draft);
    selecting.value = !draft.value;
    if (draft.value) config.value = configFrom(draft.value.data);
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
function select(id: number, event: Event) {
  if ((event.target as HTMLInputElement).checked) selected.value.push(id);
  else selected.value = selected.value.filter((item) => item !== id);
}
function move(index: number, direction: number) {
  const list = draft.value!.data.variants[orientation.value];
  const next = index + direction;
  if (next < 0 || next >= list.length) return;
  [list[index], list[next]] = [list[next], list[index]];
}
async function request<T>(path: string, method: string, body: unknown) {
  return api<T>(base.value + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
async function plan() {
  if (!editor.value || !selected.value.length) return;
  busy.value = true;
  try {
    const result = await request<{ visit: Visit; draft: StoryDraft }>(
      "/draft",
      "POST",
      {
        segment_ids: selected.value,
        match_revision: editor.value.match_revision,
        config: config.value,
      },
    );
    draft.value = copy(result.draft);
    config.value = configFrom(result.draft.data);
    selecting.value = false;
    preview.value = undefined;
    emit("updated", result.visit);
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function save(render: boolean) {
  if (!draft.value) return;
  busy.value = true;
  try {
    const data = { ...draft.value.data, ...config.value };
    const result = await request<{ visit: Visit; draft: StoryDraft }>(
      "/draft",
      "PUT",
      { revision: draft.value.revision, data },
    );
    draft.value = copy(result.draft);
    config.value = configFrom(result.draft.data);
    if (render) {
      const visit = await request<Visit>("/render", "POST", {
        revision: draft.value.revision,
      });
      emit("updated", visit);
    } else {
      emit("updated", result.visit);
      error.value = "草稿已保存，可以关闭页面后继续编辑。";
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function rematch() {
  try {
    await showConfirmDialog({
      title: "重新匹配本次游览？",
      message: "候选片段和当前编排将更新，入园照片与游览时间保留。",
      confirmButtonText: "重新匹配",
      confirmButtonColor: "#214e42",
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    emit(
      "updated",
      await request<Visit>("/retry", "POST", { action: "match" }),
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
function previewClip(clip: StoryClip) {
  const range = new URLSearchParams({
    start_seconds: String(clip.start_seconds),
    end_seconds: String(clip.end_seconds),
  });
  showPreview(`${clip.preview_url}?${range}`, clip);
}
function seek() {
  if (player.value && preview.value?.clip) player.value.currentTime = 0;
}
function timeUpdate() {
  if (!player.value || !preview.value?.clip) return;
  if (
    player.value.currentTime >=
    preview.value.clip.end_seconds - preview.value.clip.start_seconds
  )
    player.value.pause();
}
</script>
<template>
  <div class="visitor-studio">
    <div v-if="error" class="notice" role="status">
      {{ error }}
      <button class="text-button" :disabled="busy" @click="load">
        重新读取
      </button>
    </div>
    <p v-if="!editor && busy" role="status">正在读取本人的匹配画面…</p>
    <template v-if="editor">
      <div class="notice">
        <b>{{ selecting ? "挑选本人的游玩画面" : "确认您的短片编排" }}</b>
        <p>
          这里只展示本次游览匹配到的候选片段。请预览并保留本人画面，付款时再由工作人员确认。
        </p>
      </div>
      <div v-if="preview" ref="previewPanel" class="clip-preview" :class="orientation">
        <img
          v-if="preview.clip?.kind === 'image'"
          :src="preview.source"
          alt="选中片段"
        />
        <video
          v-else
          ref="player"
          :src="preview.source"
          controls
          playsinline
          preload="metadata"
          @loadedmetadata="seek"
          @timeupdate="timeUpdate"
          @error="error = '此片段暂不可播放，可重新读取或选择其他画面'"
        />
        <button class="text-button" @click="preview = undefined">
          关闭预览
        </button>
      </div>
      <template v-if="selecting">
        <p class="studio-count">
          匹配 {{ editor.segments.length }} 个片段 · 已选
          {{ selected.length }} 个
        </p>
        <div class="candidate-grid">
          <article
            v-for="(segment, index) in editor.segments"
            :key="segment.id"
            class="candidate"
          >
            <button
              class="picture"
              :aria-label="`预览候选片段 ${index + 1}`"
              @click="showPreview(segment.preview_url)"
            >
              <img
                :src="segment.thumbnail_url"
                :alt="`匹配片段 ${index + 1}`"
              /><span>▶ 预览片段</span>
            </button>
            <strong>{{ segment.camera_name || "游玩画面" }}</strong>
            <small
              >匹配度 {{ (segment.similarity * 100).toFixed(1) }}% ·
              {{ (segment.end_seconds - segment.start_seconds).toFixed(1) }}
              秒</small
            >
            <label
              ><input
                type="checkbox"
                :checked="selected.includes(segment.id)"
                :disabled="busy || !editor.editable"
                @change="select(segment.id, $event)"
              />保留片段 {{ index + 1 }}</label
            >
          </article>
        </div>
        <p v-if="!editor.segments.length" class="notice">
          暂未找到可用画面，可以重新匹配本次游览。
        </p>
      </template>
      <fieldset class="story-settings" :disabled="busy || !editor.editable">
        <legend>短片设置</legend>
        <label
          >短片标题<input
            v-model="config.title"
            maxlength="80"
            aria-label="短片标题"
        /></label>
        <label
          >故事风格<select
            v-model="config.template_id"
            :disabled="!selecting"
            aria-label="故事风格"
          >
            <option
              v-for="item in editor.catalog.templates"
              :key="item.id"
              :value="item.id"
            >
              {{ item.name }}
            </option>
          </select></label
        >
        <p v-if="!selecting" class="caption">
          更换故事风格时，请先重新挑选片段再编排。
        </p>
        <label
          >背景音乐<select v-model="config.music_id" aria-label="背景音乐">
            <option value="auto">随风格选择</option>
            <option value="none">不使用配乐</option>
            <option
              v-for="item in editor.catalog.music"
              :key="item.id"
              :value="item.id"
            >
              {{ item.name }}
            </option>
          </select></label
        >
        <audio
          v-if="
            editor.catalog.music.find((item) => item.id === config.music_id)
              ?.preview_url
          "
          :src="
            editor.catalog.music.find((item) => item.id === config.music_id)
              ?.preview_url
          "
          controls
          preload="none"
          aria-label="配乐试听"
        />
        <div class="setting-pair">
          <label
            >横版目标时长<input
              v-model.number="config.landscape_seconds"
              type="number"
              min="60"
              max="90"
              aria-label="横版目标时长" /></label
          ><label
            >竖版目标时长<input
              v-model.number="config.portrait_seconds"
              type="number"
              min="30"
              max="60"
              aria-label="竖版目标时长"
          /></label>
        </div>
        <label
          >配乐音量<input
            v-model.number="config.music_volume"
            type="range"
            min="0"
            max="1"
            step="0.05"
            aria-label="配乐音量"
        /></label>
        <p class="caption">
          素材较少时会按实际可用时长制作，画面不会重复填充。
        </p>
      </fieldset>
      <button
        v-if="selecting"
        class="primary"
        :disabled="busy || !editor.editable || !selected.length"
        @click="plan"
      >
        {{ busy ? "正在编排…" : "确认片段，编排短片" }}
      </button>
      <template v-else-if="draft">
        <div class="studio-toolbar">
          <button class="secondary" :disabled="busy" @click="selecting = true">
            重新挑选片段</button
          ><button class="text-button" @click="emit('close')">返回游览</button>
        </div>
        <fieldset class="orientation">
          <legend>编辑画幅</legend>
          <button
            v-for="item in [
              ['landscape', '横版'],
              ['portrait', '竖版'],
            ]"
            :key="item[0]"
            :aria-pressed="orientation === item[0]"
            @click="orientation = item[0] as Orientation"
          >
            {{ item[1] }}
          </button>
        </fieldset>
        <p class="studio-count">
          {{ orientation === "landscape" ? "横版" : "竖版" }} · 已选
          {{ clips.filter((clip) => clip.included).length }} 个镜头 · 素材约
          {{ duration.toFixed(1) }} 秒
        </p>
        <div
          v-for="(clip, index) in clips"
          :key="clip.id"
          class="timeline-clip"
        >
          <button
            class="picture"
            :aria-label="`预览镜头 ${index + 1}`"
            @click="previewClip(clip)"
          >
            <img :src="clip.thumbnail_url" :alt="`镜头 ${index + 1}`" /><span
              >▶ 预览</span
            >
          </button>
          <fieldset :disabled="busy || !editor.editable">
            <legend>
              镜头 {{ index + 1 }} · {{ clip.camera_name || "游玩画面" }}
            </legend>
            <label class="inline"
              ><input
                v-model="clip.included"
                type="checkbox"
                :aria-label="`保留镜头 ${index + 1}`"
              />保留此镜头</label
            >
            <div class="setting-pair">
              <label
                >开始秒数<input
                  v-model.number="clip.start_seconds"
                  type="number"
                  :min="clip.available_start_seconds"
                  :max="clip.end_seconds - 0.3"
                  step="0.1"
                  :aria-label="`镜头 ${index + 1} 开始秒数`" /></label
              ><label
                >结束秒数<input
                  v-model.number="clip.end_seconds"
                  type="number"
                  :min="clip.start_seconds + 0.3"
                  :max="clip.available_end_seconds"
                  step="0.1"
                  :aria-label="`镜头 ${index + 1} 结束秒数`"
              /></label>
            </div>
            <label
              >画面构图<select
                v-model="clip.crop_mode"
                :aria-label="`镜头 ${index + 1} 构图`"
              >
                <option value="fit">保留完整画面</option>
                <option value="focus">聚焦人物位置</option>
                <option v-if="clip.crop_mode === 'track'" value="track">
                  自动人物跟随
                </option>
              </select></label
            >
            <template v-if="clip.crop_mode === 'focus'"
              ><label
                >人物横向位置<input
                  v-model.number="clip.focus_x"
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  :aria-label="`镜头 ${index + 1} 横向焦点`" /></label
              ><label
                >人物纵向位置<input
                  v-model.number="clip.focus_y"
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  :aria-label="`镜头 ${index + 1} 纵向焦点`" /></label
            ></template>
            <label v-if="clip.has_audio" class="inline"
              ><input
                v-model="clip.keep_audio"
                type="checkbox"
              />保留原声</label
            >
            <label class="inline"
              ><input
                type="radio"
                v-model="draft.data.cover_clip_id"
                :value="clip.id"
                name="cover"
              />作为封面</label
            >
            <div class="studio-toolbar">
              <button
                class="text-button"
                :disabled="index === 0"
                :aria-label="`提前镜头 ${index + 1}`"
                @click="move(index, -1)"
              >
                ↑ 提前</button
              ><button
                class="text-button"
                :disabled="index === clips.length - 1"
                :aria-label="`后移镜头 ${index + 1}`"
                @click="move(index, 1)"
              >
                ↓ 后移
              </button>
            </div>
          </fieldset>
        </div>
        <p v-for="warning in draft.data.warnings" :key="warning" class="notice">
          {{ warning }}
        </p>
        <button
          class="secondary"
          :disabled="busy || !editor.editable"
          @click="save(false)"
        >
          保存草稿
        </button>
        <button
          class="primary"
          :disabled="busy || !editor.editable"
          @click="save(true)"
        >
          {{ busy ? "正在提交…" : "确认制作横竖版" }}
        </button>
      </template>
      <button
        class="text-button"
        :disabled="busy || !editor.editable"
        @click="rematch"
      >
        重新匹配本次游览
      </button>
    </template>
  </div>
</template>
<style scoped>
.visitor-studio {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.visitor-studio p {
  margin: 8px 0;
}
.studio-count {
  font-size: 14px;
  color: #537066;
}
.candidate-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.candidate,
.timeline-clip {
  min-width: 0;
  border: 1px solid #dce5df;
  border-radius: 12px;
  padding: 10px;
  background: #fff;
}
.candidate strong,
.candidate small {
  display: block;
  margin: 8px 0;
}
.candidate small {
  color: #6c7d72;
  font-size: 12px;
}
.picture {
  position: relative;
  padding: 0;
  overflow: hidden;
  border-radius: 8px;
  border: 0;
  display: block;
  width: 100%;
  background: #e6eee8;
}
.picture img {
  display: block;
  width: 100%;
  aspect-ratio: 4/3;
  object-fit: cover;
}
.picture span {
  position: absolute;
  left: 5px;
  bottom: 5px;
  background: #163a30cc;
  color: #fff;
  padding: 4px 6px;
  border-radius: 5px;
  font-size: 11px;
}
.story-settings,
.timeline-clip fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
  display: grid;
  gap: 12px;
}
.story-settings legend,
.timeline-clip legend {
  font-weight: 600;
  margin: 12px 0;
}
.story-settings label,
.timeline-clip label {
  display: grid;
  gap: 6px;
  font-size: 13px;
  color: #466657;
}
input:not([type="checkbox"]):not([type="radio"]):not([type="range"]),
select {
  width: 100%;
  min-width: 0;
  border: 1px solid #d4ded7;
  background: #fff;
  border-radius: 8px;
  padding: 10px;
  font: inherit;
  box-sizing: border-box;
}
input[type="checkbox"],
input[type="radio"],
input[type="range"] {
  accent-color: #214e42;
}
.inline {
  display: flex !important;
  align-items: center;
  gap: 8px;
}
.setting-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.studio-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.clip-preview {
  min-width: 0;
  background: #eef3ee;
  border-radius: 12px;
  padding: 10px;
}
.clip-preview video,
.clip-preview img {
  width: 100%;
  max-height: 440px;
  object-fit: contain;
  display: block;
}
.clip-preview.portrait video {
  aspect-ratio: 9/16;
}
.clip-preview.landscape video {
  aspect-ratio: 16/9;
}
audio {
  width: 100%;
}
button:disabled {
  opacity: 0.5;
}
@media (min-width: 700px) {
  .candidate-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .timeline-clip {
    display: grid;
    grid-template-columns: 180px minmax(0, 1fr);
    align-items: start;
    gap: 20px;
  }
}
</style>
