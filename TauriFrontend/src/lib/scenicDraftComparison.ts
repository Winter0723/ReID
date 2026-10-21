import type { EditingCatalog, EditingClip, EditingDraft, VlogOrientation } from "./api/scenicEditing";

export interface DraftDifference {
  key: string;
  label: string;
  before: string;
  after: string;
}

export interface ComparedClip {
  id: string;
  label: string;
  included: boolean;
}

export interface VariantComparison {
  orientation: VlogOrientation;
  saved: { clipCount: number; durationSeconds: number };
  current: { clipCount: number; durationSeconds: number };
  added: ComparedClip[];
  removed: ComparedClip[];
  reordered: boolean;
  savedOrder: string[];
  currentOrder: string[];
  changes: Array<ComparedClip & { fields: DraftDifference[] }>;
  target: DraftDifference[];
  hasChanges: boolean;
}

export interface DraftComparison {
  settings: DraftDifference[];
  variants: VariantComparison[];
  hasChanges: boolean;
}

function numberText(value: number): string {
  return Number.isFinite(value) ? String(Math.round(value * 1000) / 1000) : "未填写";
}

/** Uses the renderer's adjacent-clip dissolve rule, including short clips. */
export function scenicTimelineDuration(clips: readonly EditingClip[], transitionSeconds = 0): number {
  const durations = clips.filter((clip) => clip.included).map((clip) => {
    const duration = clip.end_seconds - clip.start_seconds;
    return Number.isFinite(duration) ? Math.max(0, duration) : 0;
  });
  const transition = Number.isFinite(transitionSeconds) ? Math.max(0, transitionSeconds) : 0;
  return Math.max(0, durations.reduce((total, duration, index) => total + duration
    - (index ? Math.min(transition, durations[index - 1] / 3, duration / 3) : 0), 0));
}

function clipLabel(clip: EditingClip): string {
  return `${clip.camera_name || clip.location_name || clip.title || "游览时刻"} · ${clip.kind === "image" ? "照片" : "视频"}`;
}

function describeClip(clip: EditingClip): ComparedClip {
  return { id: clip.id, label: clipLabel(clip), included: clip.included };
}

function difference(fields: DraftDifference[], key: string, label: string, before: unknown, after: unknown,
  format: (value: any) => string = (value) => String(value ?? "") || "未设置"): void {
  if (before !== after) fields.push({ key, label, before: format(before), after: format(after) });
}

function compareClips(saved: EditingClip, current: EditingClip): DraftDifference[] {
  const fields: DraftDifference[] = [];
  const enabled = (value: boolean) => value ? "是" : "否";
  difference(fields, "included", "入片选择", saved.included, current.included, enabled);
  difference(fields, "start_seconds", "裁切入点", saved.start_seconds, current.start_seconds, (value) => `${numberText(value)} 秒`);
  difference(fields, "end_seconds", "裁切出点", saved.end_seconds, current.end_seconds, (value) => `${numberText(value)} 秒`);
  difference(fields, "crop_mode", "构图方式", saved.crop_mode, current.crop_mode,
    (value: EditingClip["crop_mode"]) => ({ fit: "完整画面填充", focus: "手动焦点裁切", track: "人物跟随" })[value]);
  difference(fields, "focus_x", "水平焦点", saved.focus_x, current.focus_x, numberText);
  difference(fields, "focus_y", "垂直焦点", saved.focus_y, current.focus_y, numberText);
  difference(fields, "keep_audio", "保留原声", saved.keep_audio, current.keep_audio, enabled);
  difference(fields, "locked", "锁定镜头", saved.locked, current.locked, enabled);
  difference(fields, "title", "镜头字幕", saved.title || "", current.title || "");
  difference(fields, "location_name", "地点字幕", saved.location_name || "", current.location_name || "");
  // Only the date is rendered in the film; metadata time changes alone do not change captions.
  difference(fields, "recorded_at", "日期字幕", saved.recorded_at?.slice(0, 10) || "", current.recorded_at?.slice(0, 10) || "");
  return fields;
}

/** Read-only editing comparison; deliberately excludes asset URLs and analysis metadata. */
export function compareScenicDrafts(saved: EditingDraft, current: EditingDraft, catalog: EditingCatalog): DraftComparison {
  const settings: DraftDifference[] = [];
  const templateName = (value: string) => catalog.templates.find((template) => template.id === value)?.name || value;
  const musicName = (value: string) => !value || value === "none" ? "关闭配乐"
    : catalog.music.find((music) => music.id === value)?.name || value;
  const coverName = (value: string, draft: EditingDraft) => {
    const clip = [...draft.sources, ...draft.variants.landscape, ...draft.variants.portrait].find((item) => item.id === value);
    return clip ? clipLabel(clip) : value || "自动选择";
  };
  difference(settings, "template_id", "剪辑风格", saved.template_id, current.template_id, templateName);
  difference(settings, "template_version", "模板版本", saved.template_version, current.template_version);
  difference(settings, "title", "影片标题", saved.title, current.title);
  difference(settings, "brand", "景区署名", saved.brand, current.brand);
  difference(settings, "ending_title", "片尾标题", saved.ending_title || "", current.ending_title || "");
  difference(settings, "music_id", "配乐", saved.music_id || "none", current.music_id || "none", musicName);
  difference(settings, "music_volume", "音乐音量", saved.music_volume, current.music_volume, (value) => `${numberText(value * 100)}%`);
  difference(settings, "original_volume", "原声音量", saved.original_volume, current.original_volume, (value) => `${numberText(value * 100)}%`);
  if ((saved.cover_clip_id || "") !== (current.cover_clip_id || "")) settings.push({ key: "cover_clip_id", label: "封面镜头",
    before: coverName(saved.cover_clip_id || "", saved), after: coverName(current.cover_clip_id || "", current) });
  difference(settings, "cover_time_seconds", "封面时间", saved.cover_time_seconds ?? null, current.cover_time_seconds ?? null,
    (value) => value === null ? "自动选择" : `${numberText(value)} 秒`);

  const variants = (["landscape", "portrait"] as const).map((orientation): VariantComparison => {
    const before = saved.variants[orientation];
    const after = current.variants[orientation];
    const beforeById = new Map(before.map((clip) => [clip.id, clip]));
    const afterById = new Map(after.map((clip) => [clip.id, clip]));
    const added = after.filter((clip) => !beforeById.has(clip.id)).map(describeClip);
    const removed = before.filter((clip) => !afterById.has(clip.id)).map(describeClip);
    // Compare the relative order of shared clips so insertions/deletions do not imply reordering.
    const beforeCommon = before.filter((clip) => afterById.has(clip.id));
    const afterCommon = after.filter((clip) => beforeById.has(clip.id));
    const reordered = beforeCommon.some((clip, index) => clip.id !== afterCommon[index].id);
    const changes = after.flatMap((clip) => {
      const previous = beforeById.get(clip.id);
      const fields = previous ? compareClips(previous, clip) : [];
      return fields.length ? [{ ...describeClip(clip), fields }] : [];
    });
    const target: DraftDifference[] = [];
    difference(target, "target_seconds", "目标时长", saved[`${orientation}_seconds`], current[`${orientation}_seconds`], (value) => `${numberText(value)} 秒`);
    const summary = (draft: EditingDraft) => ({
      clipCount: draft.variants[orientation].filter((clip) => clip.included).length,
      durationSeconds: scenicTimelineDuration(draft.variants[orientation], catalog.templates.find((template) => template.id === draft.template_id)?.transition_seconds),
    });
    return { orientation, saved: summary(saved), current: summary(current), added, removed, reordered,
      savedOrder: beforeCommon.map(clipLabel), currentOrder: afterCommon.map(clipLabel), changes, target,
      hasChanges: Boolean(added.length || removed.length || reordered || changes.length || target.length) };
  });
  return { settings, variants, hasChanges: Boolean(settings.length || variants.some((variant) => variant.hasChanges)) };
}
