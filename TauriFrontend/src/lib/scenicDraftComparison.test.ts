import { describe, expect, it } from "vitest";
import type { EditingCatalog, EditingClip, EditingDraft } from "./api/scenicEditing";
import { compareScenicDrafts, scenicTimelineDuration } from "./scenicDraftComparison";

const catalog: EditingCatalog = { enabled: true, templates: [
  { id: "documentary", name: "旅行纪实", version: "1", description: "", music_id: "travel", min_clip_seconds: 3, max_clip_seconds: 8, color: "#eee", transition_seconds: .3 },
  { id: "upbeat", name: "轻快打卡", version: "1", description: "", music_id: "steps", min_clip_seconds: 2, max_clip_seconds: 4, color: "#eee", transition_seconds: 0 },
], music: [{ id: "travel", name: "旅途" }, { id: "steps", name: "脚步" }] };

function clip(id: string, values: Partial<EditingClip> = {}): EditingClip {
  return { id, media_id: 1, object_key: `${id}.mp4`, source_sha256: id, kind: "video", start_seconds: 0, end_seconds: 6,
    camera_name: `点位 ${id}`, included: true, locked: false, keep_audio: false, crop_mode: "fit", focus_x: .5, focus_y: .5, ...values };
}
function fixture(): EditingDraft {
  const clips = [clip("a"), clip("b")];
  return { schema_version: 1, template_id: "documentary", template_version: "1", title: "游览记忆", brand: "", music_id: "travel",
    music_volume: .25, original_volume: 1, landscape_seconds: 75, portrait_seconds: 45, cover_clip_id: "a", warnings: [],
    sources: structuredClone(clips), variants: { landscape: structuredClone(clips), portrait: structuredClone(clips) } };
}

describe("draft comparison", () => {
  it("ignores signed URL renewal and non-edit analysis metadata", () => {
    const saved = fixture();
    const current = structuredClone(saved);
    for (const item of [...current.sources, ...current.variants.landscape, ...current.variants.portrait]) {
      item.thumbnail_url = "/signed/thumb?signature=new";
      item.original_media_url = "/signed/video?signature=new";
      item.quality = { sharpness: 100 };
      item.trajectory = [{ frame: 1, box: [.1, .1, .5, .5] }];
      item.width = 1920;
      item.camera_name = "更新的内部设备名";
    }
    current.warnings = ["分析结果已更新"];
    expect(compareScenicDrafts(saved, current, catalog).hasChanges).toBe(false);
  });
  it("keeps landscape edits independent of portrait and compares exact editable values", () => {
    const saved = fixture();
    const current = structuredClone(saved);
    Object.assign(current.variants.landscape[0], { start_seconds: 1.5, end_seconds: 5, crop_mode: "focus", focus_x: .3, focus_y: .4,
      keep_audio: true, locked: true, title: "湖边留影", location_name: "湖畔", recorded_at: "2026-09-12T12:00:00" });
    const [landscape, portrait] = compareScenicDrafts(saved, current, catalog).variants;
    expect(landscape.current.durationSeconds).toBeCloseTo(9.2);
    expect(landscape.changes[0].fields.map((field) => field.key)).toEqual([
      "start_seconds", "end_seconds", "crop_mode", "focus_x", "focus_y", "keep_audio", "locked", "title", "location_name", "recorded_at",
    ]);
    expect(portrait.hasChanges).toBe(false);
    expect(portrait.current.durationSeconds).toBeCloseTo(11.7);
    expect(saved.variants.landscape[0].start_seconds).toBe(0);
  });
  it("tracks additions, removals and actual reordering without treating an insertion as a move", () => {
    const saved = fixture();
    const current = structuredClone(saved);
    current.variants.landscape.unshift(clip("c"));
    current.variants.portrait.pop();
    let [landscape, portrait] = compareScenicDrafts(saved, current, catalog).variants;
    expect(landscape.added.map((item) => item.id)).toEqual(["c"]);
    expect(landscape.reordered).toBe(false);
    expect(portrait.removed.map((item) => item.id)).toEqual(["b"]);
    expect(portrait.reordered).toBe(false);
    current.variants.landscape.reverse();
    [landscape] = compareScenicDrafts(saved, current, catalog).variants;
    expect(landscape.reordered).toBe(true);
    expect(landscape.savedOrder).toEqual(["点位 a · 视频", "点位 b · 视频"]);
    expect(landscape.currentOrder).toEqual(["点位 b · 视频", "点位 a · 视频"]);
  });
  it("reports exclusion as an edit while counting only included clips", () => {
    const saved = fixture();
    const current = structuredClone(saved);
    current.variants.landscape[0].included = false;
    const [landscape, portrait] = compareScenicDrafts(saved, current, catalog).variants;
    expect(landscape.saved.clipCount).toBe(2);
    expect(landscape.current).toEqual({ clipCount: 1, durationSeconds: 6 });
    expect(landscape.changes[0].fields).toEqual([{ key: "included", label: "入片选择", before: "是", after: "否" }]);
    expect(landscape.removed).toEqual([]);
    expect(portrait.current.clipCount).toBe(2);
  });
  it("compares title, template, music, cover and separate duration targets", () => {
    const saved = fixture();
    const current = structuredClone(saved);
    Object.assign(current, { template_id: "upbeat", title: "周末旅程", brand: "景区", music_id: "steps", music_volume: .5,
      original_volume: .75, cover_clip_id: "b", portrait_seconds: 50 });
    const result = compareScenicDrafts(saved, current, catalog);
    expect(result.settings.map((field) => field.key)).toEqual(["template_id", "title", "brand", "music_id", "music_volume", "original_volume", "cover_clip_id"]);
    expect(result.settings[0]).toMatchObject({ before: "旅行纪实", after: "轻快打卡" });
    expect(result.settings.find((field) => field.key === "cover_clip_id")).toMatchObject({ before: "点位 a · 视频", after: "点位 b · 视频" });
    expect(result.variants[0].current.durationSeconds).toBe(12);
    expect(result.variants[0].target).toEqual([]);
    expect(result.variants[1].target[0]).toMatchObject({ before: "45 秒", after: "50 秒" });
  });
  it("does not mutate either snapshot or expose editable references in its output", () => {
    const saved = fixture();
    const current = structuredClone(saved);
    current.title = "未保存的标题";
    current.variants.landscape.reverse();
    current.variants.landscape[0].locked = true;
    const savedBefore = structuredClone(saved);
    const currentBefore = structuredClone(current);
    const result = compareScenicDrafts(saved, current, catalog);
    result.settings[0].after = "修改报告";
    result.variants[0].currentOrder.reverse();
    result.variants[0].changes[0].label = "修改报告标签";
    expect(saved).toEqual(savedBefore);
    expect(current).toEqual(currentBefore);
  });
  it("treats absent optional captions as empty and ignores time-only metadata changes", () => {
    const saved = fixture();
    saved.variants.landscape[0].recorded_at = "2026-09-12T09:00:00";
    const current = structuredClone(saved);
    current.variants.landscape[0].recorded_at = "2026-09-12T15:00:00";
    current.variants.landscape[0].title = "";
    expect(compareScenicDrafts(saved, current, catalog).hasChanges).toBe(false);
  });
});

describe("timeline duration", () => {
  it("subtracts bounded overlaps across included neighbors, including very short clips", () => {
    expect(scenicTimelineDuration([clip("a", { end_seconds: .3 }), clip("excluded", { included: false, end_seconds: 50 }), clip("b")], .45)).toBeCloseTo(6.2);
    expect(scenicTimelineDuration([], .45)).toBe(0);
    expect(scenicTimelineDuration([clip("a", { included: false })], .45)).toBe(0);
  });
});