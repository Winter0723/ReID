<script setup lang="ts">
import { computed } from "vue";
import type { DraftRecord, EditingCatalog } from "../../lib/api/scenicEditing";
import { compareScenicDrafts } from "../../lib/scenicDraftComparison";

const props = defineProps<{ current: DraftRecord; saved: DraftRecord; catalog: EditingCatalog; dirty: boolean }>();
const comparison = computed(() => compareScenicDrafts(props.saved.data, props.current.data, props.catalog));
</script>

<template>
  <div class="draft-comparison">
    <p class="comparison-direction">已保存 #{{ saved.id }} · 修订 {{ saved.revision }} → 当前 #{{ current.id }}{{ dirty ? '（含未保存修改）' : '' }}</p>
    <p class="comparison-note">只读比较，右侧为当前编辑。预计时长已扣除相邻镜头叠化。</p>
    <p v-if="!comparison.hasChanges" class="comparison-identical" role="status">当前草稿与所选草稿的编辑内容一致</p>
    <div v-if="comparison.settings.length" class="settings-comparison" aria-label="影片设置变化">
      <h4>影片设置变化</h4>
      <dl class="difference-list">
        <div v-for="field in comparison.settings" :key="field.key" class="difference-row">
          <dt>{{ field.label }}</dt><dd><span class="before">{{ field.before }}</span><span class="change-arrow" aria-label="变为">→</span><span class="after">{{ field.after }}</span></dd>
        </div>
      </dl>
    </div>
    <div class="variant-comparisons">
      <article v-for="variant in comparison.variants" :key="variant.orientation" class="variant-comparison" :aria-label="variant.orientation === 'landscape' ? '横版比较' : '竖版比较'">
        <h4>{{ variant.orientation === 'landscape' ? '横版 16:9' : '竖版 9:16' }}</h4>
        <div class="duration-comparison">
          <div><span>已保存</span><strong>{{ variant.saved.clipCount }} 个镜头 · {{ variant.saved.durationSeconds.toFixed(1) }} 秒</strong></div>
          <span class="change-arrow" aria-hidden="true">→</span>
          <div><span>当前编辑</span><strong>{{ variant.current.clipCount }} 个镜头 · {{ variant.current.durationSeconds.toFixed(1) }} 秒</strong></div>
        </div>
        <p class="variant-summary">新增 {{ variant.added.length }} · 移除 {{ variant.removed.length }} · 调整 {{ variant.changes.length }}{{ variant.reordered ? ' · 顺序已调整' : '' }}</p>
        <p v-if="!variant.hasChanges" class="comparison-note">此画幅的镜头与目标时长未变</p>
        <dl v-if="variant.target.length" class="difference-list"><div v-for="field in variant.target" :key="field.key" class="difference-row"><dt>{{ field.label }}</dt><dd><span class="before">{{ field.before }}</span><span class="change-arrow">→</span><span class="after">{{ field.after }}</span></dd></div></dl>
        <div v-if="variant.added.length" class="clip-list added"><h5>新增镜头</h5><p v-for="clip in variant.added" :key="clip.id">{{ clip.label }}{{ clip.included ? '' : '（未入片）' }}</p></div>
        <div v-if="variant.removed.length" class="clip-list removed"><h5>移除镜头</h5><p v-for="clip in variant.removed" :key="clip.id">{{ clip.label }}{{ clip.included ? '' : '（原未入片）' }}</p></div>
        <details v-if="variant.reordered" class="clip-changes">
          <summary>查看共有镜头顺序变化</summary><p class="before">已保存：{{ variant.savedOrder.join(' → ') }}</p><p class="after">当前：{{ variant.currentOrder.join(' → ') }}</p>
        </details>
        <details v-for="clip in variant.changes" :key="clip.id" class="clip-changes" open>
          <summary>{{ clip.label }}{{ clip.included ? '' : '（未入片）' }}</summary>
          <dl class="difference-list"><div v-for="field in clip.fields" :key="field.key" class="difference-row"><dt>{{ field.label }}</dt><dd><span class="before">{{ field.before }}</span><span class="change-arrow" aria-label="变为">→</span><span class="after">{{ field.after }}</span></dd></div></dl>
        </details>
      </article>
    </div>
  </div>
</template>

<style scoped>
.draft-comparison { min-width: 0; color: #d9eee5; font-size: 12px; overflow-wrap: anywhere; }
.comparison-direction { margin: 0 0 6px; color: #b7e4d6; line-height: 1.6; }
.comparison-note { margin: 6px 0; color: #9fb7ac; font-size: 11px; line-height: 1.6; }
.comparison-identical { padding: 10px 12px; background: #15372f; border-radius: 6px; color: #a7f3d0; }
.settings-comparison { margin-top: 14px; padding: 12px; border: 1px solid #2b463d; border-radius: 8px; background: #0d1915; }
.draft-comparison h4 { margin: 0 0 10px; font-size: 13px; color: #dcf9ec; }
.variant-comparisons { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,300px),1fr)); gap: 12px; margin-top: 12px; }
.variant-comparison { min-width: 0; padding: 12px; border: 1px solid #2b463d; border-radius: 8px; background: #0b1613; }
.duration-comparison { display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr); gap: 8px; align-items: center; }
.duration-comparison div { min-width: 0; display: grid; gap: 5px; }
.duration-comparison div>span { font-size: 10px; color: #9fb7ac; }
.duration-comparison strong { font-size: 12px; line-height: 1.6; font-weight: 500; }
.variant-summary { margin: 12px 0 8px; color: #7ed9bd; font-size: 11px; }
.difference-list { display: grid; gap: 8px; margin: 0; }
.difference-row { min-width: 0; display: grid; grid-template-columns: 76px minmax(0,1fr); gap: 6px; align-items: start; line-height: 1.6; }
.difference-row dt { color: #9fb7ac; font-size: 11px; }
.difference-row dd { margin: 0; min-width: 0; display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr); gap: 8px; }
.before { color: #bac5be; }.after { color: #a7f3d0; }.change-arrow { color: #6c9587; }
.clip-list { margin-top: 12px; padding-top: 10px; border-top: 1px solid #243c33; }
.clip-list h5 { margin: 0 0 5px; font-size: 11px; font-weight: 500; }
.clip-list p { margin: 3px 0; font-size: 11px; line-height: 1.6; }
.added { color: #a7f3d0; }.removed { color: #f1cda7; }
.clip-changes { margin-top: 12px; padding-top: 10px; border-top: 1px solid #243c33; }
.clip-changes summary { margin-bottom: 9px; cursor: pointer; font-size: 11px; line-height: 1.6; }
.clip-changes p { margin: 5px 0; font-size: 11px; line-height: 1.7; }
@media(max-width:700px) { .difference-row { grid-template-columns: 1fr; gap: 2px; } }
</style>
