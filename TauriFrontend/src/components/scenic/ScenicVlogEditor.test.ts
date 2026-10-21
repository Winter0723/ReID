import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ScenicVlogEditor from "./ScenicVlogEditor.vue";
import { scenicEditingApi, type DraftRecord, type EditingCatalog } from "../../lib/api/scenicEditing";
import { scenicApi } from "../../lib/api/scenic";
import { useScenicStore } from "../../stores/scenic";

const mock = vi.hoisted(() => ({ store: null as any }));
vi.mock("../../stores/workbench", () => ({ useWorkbenchStore: () => mock.store }));
vi.mock("../../lib/api/scenicEditing", () => ({ scenicEditingApi: { drafts: vi.fn(), auto: vi.fn(), save: vi.fn() } }));
vi.mock("../../lib/api/scenic", () => ({ scenicApi: { generateVlog: vi.fn() } }));
vi.mock("../../lib/videoPreview", () => ({ resolveVideoPreviewSource: vi.fn(async () => ({ source: "" })) }));
const catalog: EditingCatalog = { enabled: true, templates: [{ id: "documentary", name: "旅行纪实", version: "1", description: "旅行", music_id: "travel", min_clip_seconds: 3, max_clip_seconds: 8, color: "#eee" }, { id: "upbeat", name: "轻快打卡", version: "1", description: "轻快", music_id: "steps", min_clip_seconds: 2, max_clip_seconds: 4, color: "#eee" }], music: [{ id: "travel", name: "旅途" }] };
function fixture(id = 1): DraftRecord {
  const clip = { id: "a", media_id: 10, object_key: "a.mp4", source_sha256: "hash", kind: "video" as const, start_seconds: 0, end_seconds: 7, camera_name: "花园", locked: false, included: true, keep_audio: false, focus_x: .5, focus_y: .5, crop_mode: "fit" as const };
  const second = { ...clip, id: "b", camera_name: "湖畔" };
  return { id, session_id: id, revision: 1, data: { schema_version: 1, template_id: "documentary", template_version: "1", title: `游客 ${id}`, brand: "", music_id: "travel", music_volume: .25, original_volume: 1, landscape_seconds: 75, portrait_seconds: 45, sources: [clip, second], variants: { landscape: [clip, second], portrait: [{ ...clip }, { ...second }] }, warnings: [] } };
}
describe("scenic editor", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    mock.store = reactive({ scenicProjectId: 7, selectedTourist: { id: 1, ticket_code: "T1" }, selectTourist: vi.fn() });
    vi.mocked(scenicEditingApi.drafts).mockImplementation(async (id) => ({ drafts: [fixture(id)] }));
    vi.mocked(scenicEditingApi.save).mockImplementation(async (_id, record) => ({ ...JSON.parse(JSON.stringify(record)), revision: record.revision + 1 }));
    vi.mocked(scenicApi.generateVlog).mockResolvedValue({ status: "queued", job_id: "x", vlog: { id: 10, status: "queued" } });
  });
  it("keeps edits across polling, tourist switches and remounts", async () => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('[aria-label="影片标题"]').setValue("未保存的湖畔记忆");
    mock.store.selectedTourist = { id: 1, ticket_code: "T1", status: "rendering" };
    await flushPromises();
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("未保存的湖畔记忆");
    mock.store.selectedTourist = { id: 2, ticket_code: "T2" };
    await flushPromises();
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("游客 2");
    mock.store.selectedTourist = { id: 1, ticket_code: "T1" };
    await flushPromises();
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("未保存的湖畔记忆");
    wrapper.unmount();
    const reopened = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    expect(reopened.text()).toContain("有未保存修改");
    expect((reopened.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("未保存的湖畔记忆");
  });
  it("edits each orientation independently and submits the saved revision", async () => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('[aria-label="后移镜头 1"]').trigger("click");
    await wrapper.get('[aria-label="镜头入点"]').setValue(1.5);
    const buffer = useScenicStore().editingBuffers["7:1"];
    expect(buffer.record.data.variants.landscape[0].id).toBe("b");
    expect(buffer.record.data.variants.portrait[0].id).toBe("a");
    const submit = wrapper.findAll("button").find((button) => button.text() === "生成横竖双版")!;
    await submit.trigger("click");
    await flushPromises();
    expect(scenicEditingApi.save).toHaveBeenCalledOnce();
    expect(scenicApi.generateVlog).toHaveBeenCalledWith(1, { draft_id: 1, revision: 2 });
    expect(wrapper.text()).not.toContain("有未保存修改");
  });
  it.each(["landscape", "portrait"] as const)("excludes a timeline clip from both saved variants while editing %s", async (orientation) => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    if (orientation === "portrait") await wrapper.findAll("button").find((button) => button.text() === "竖版 9:16")!.trigger("click");
    // The other orientation keeps a different order; link by source identity.
    await wrapper.get('[aria-label="后移镜头 1"]').trigger("click");
    await wrapper.get('[aria-label="包含镜头 1"]').setValue(false);
    expect(wrapper.get('[aria-label="编辑镜头 1"]').text()).toContain("已排除");
    expect(wrapper.get(".timeline-summary").text()).toContain("1 个镜头");
    const otherTab = orientation === "portrait" ? "横版 16:9" : "竖版 9:16";
    await wrapper.findAll("button").find((button) => button.text() === otherTab)!.trigger("click");
    expect((wrapper.get('[aria-label="包含镜头 2"]').element as HTMLInputElement).checked).toBe(false);
    expect(wrapper.get('[aria-label="编辑镜头 2"]').text()).toContain("已排除");
    await wrapper.findAll("button").find((button) => button.text() === "生成横竖双版")!.trigger("click");
    await flushPromises();
    const submitted = vi.mocked(scenicEditingApi.save).mock.calls[0][1].data;
    for (const clips of Object.values(submitted.variants)) {
      expect(clips.find((clip) => clip.id === "b")?.included).toBe(false);
      expect(clips.filter((clip) => clip.included).map((clip) => clip.id)).toEqual(["a"]);
    }
    expect(scenicApi.generateVlog).toHaveBeenCalledWith(1, { draft_id: 1, revision: 2 });
  });
  it("restores both variants from the timeline without changing their separate edits", async () => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('[aria-label="镜头入点"]').setValue(1.5);
    await wrapper.get('[aria-label="包含镜头 1"]').setValue(false);
    await wrapper.get('[aria-label="包含镜头 1"]').setValue(true);
    const variants = useScenicStore().editingBuffers["7:1"].record.data.variants;
    expect(variants.landscape[0]).toMatchObject({ included: true, start_seconds: 1.5 });
    expect(variants.portrait[0]).toMatchObject({ included: true, start_seconds: 0 });
  });
  it("keeps inspector inclusion specific to one orientation and shows a mixed timeline selection", async () => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('.clip-inspector input[type="checkbox"]').setValue(false);
    const variants = useScenicStore().editingBuffers["7:1"].record.data.variants;
    expect(variants.landscape[0].included).toBe(false);
    expect(variants.portrait[0].included).toBe(true);
    const checkbox = wrapper.get('[aria-label="包含镜头 1"]');
    expect((checkbox.element as HTMLInputElement).indeterminate).toBe(true);
    await checkbox.setValue(false);
    expect(variants.portrait[0].included).toBe(false);
    expect((checkbox.element as HTMLInputElement).indeterminate).toBe(false);
  });
  it("protects a clip locked in the other orientation from a shared exclusion", async () => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    const variants = useScenicStore().editingBuffers["7:1"].record.data.variants;
    variants.portrait[0].locked = true;
    await flushPromises();
    expect(wrapper.get('[aria-label="包含镜头 1"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[aria-label="包含镜头 1"]').element.parentElement?.title).toContain("解除横竖版中的锁定");
    expect(variants.landscape[0].included).toBe(true);
    expect(variants.portrait[0].included).toBe(true);
  });
  it("preserves the local edit and refuses generation after a revision conflict", async () => {
    vi.mocked(scenicEditingApi.save).mockRejectedValue(new Error("草稿已更新，请重新读取"));
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('[aria-label="影片标题"]').setValue("保留此编辑");
    await wrapper.findAll("button").find((button) => button.text() === "生成横竖双版")!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("草稿已更新");
    expect(scenicApi.generateVlog).not.toHaveBeenCalled();
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("保留此编辑");
    await wrapper.findAll("button").find((button) => button.text() === "备份本地修改并读取最新")!.trigger("click");
    await flushPromises();
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("游客 1");
    await wrapper.findAll("button").find((button) => button.text() === "恢复本地备份")!.trigger("click");
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("保留此编辑");
  });
  it("saves edits using the original template before generating a new template", async () => {
    vi.mocked(scenicEditingApi.auto).mockResolvedValue({ ...fixture(3), data: { ...fixture(3).data, template_id: "upbeat", music_id: "steps" } });
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('[aria-label="影片标题"]').setValue("待保存的修改");
    await wrapper.get('[aria-label="剪辑风格"]').setValue("upbeat");
    expect(wrapper.findAll("button").find((button) => button.text() === "生成横竖双版")!.attributes("disabled")).toBeDefined();
    await wrapper.findAll("button").find((button) => button.text() === "重新自动编排")!.trigger("click");
    await flushPromises();
    expect(scenicEditingApi.save).toHaveBeenCalledWith(1, expect.objectContaining({ data: expect.objectContaining({ template_id: "documentary", title: "待保存的修改" }) }));
    expect(scenicEditingApi.auto).toHaveBeenCalledWith(1, expect.objectContaining({ template_id: "upbeat", music_id: "steps", previous_draft_id: 1 }));
  });
  it("uses the signed original photo and renews asset URLs without losing unsaved changes", async () => {
    let signing = "first";
    vi.mocked(scenicEditingApi.drafts).mockImplementation(async (id) => {
      const value = fixture(id);
      for (const clip of [...value.data.sources, ...value.data.variants.landscape, ...value.data.variants.portrait]) {
        clip.kind = "image";
        clip.thumbnail_url = `/signed/thumbnail?token=${signing}`;
        clip.original_media_url = `/signed/content?token=${signing}`;
      }
      return { drafts: [value] };
    });
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    expect(wrapper.get('img[alt="当前镜头构图"]').attributes("src")).toContain("/signed/content?token=first");
    expect(wrapper.text()).not.toContain("有未保存修改");
    await wrapper.get('[aria-label="影片标题"]').setValue("保留草稿");
    mock.store.selectedTourist = { id: 2 };
    await flushPromises();
    signing = "renewed";
    mock.store.selectedTourist = { id: 1 };
    await flushPromises();
    expect(wrapper.get('img[alt="当前镜头构图"]').attributes("src")).toContain("/signed/content?token=renewed");
    expect((wrapper.get('[aria-label="影片标题"]').element as HTMLInputElement).value).toBe("保留草稿");
    expect(wrapper.text()).toContain("有未保存修改");
  });
  it("compares unsaved edits to saved versions without opening or mutating them", async () => {
    const original = fixture(1);
    const other = fixture(9);
    other.session_id = 1;
    other.data.title = "另一份已保存草稿";
    vi.mocked(scenicEditingApi.drafts).mockResolvedValue({ drafts: [original, other] });
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.get('[aria-label="影片标题"]').setValue("正在修改的标题");
    await wrapper.get('[aria-label="镜头入点"]').setValue(1.5);
    const cache = useScenicStore();
    const before = JSON.stringify(cache.editingBuffers["7:1"]);
    await wrapper.findAll("button").find((button) => button.text() === "比较草稿")!.trigger("click");
    const comparison = wrapper.get('[aria-label="草稿版本比较"]');
    expect(comparison.text()).toContain("含未保存修改");
    expect(comparison.text()).toContain("正在修改的标题");
    expect(comparison.get('[aria-label="横版比较"]').text()).toContain("裁切入点");
    expect(comparison.get('[aria-label="竖版比较"]').text()).not.toContain("裁切入点");
    await comparison.get('[aria-label="比较基准草稿"]').setValue("9");
    expect(comparison.text()).toContain("另一份已保存草稿");
    expect(JSON.stringify(cache.editingBuffers["7:1"])).toBe(before);
    expect(original.data.title).toBe("游客 1");
    expect(other.data.title).toBe("另一份已保存草稿");
    expect(scenicEditingApi.save).not.toHaveBeenCalled();
    expect(scenicEditingApi.auto).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it("clears the comparison on tourist switches and does not reopen stale comparisons", async () => {
    const wrapper = mount(ScenicVlogEditor, { props: { catalog } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "比较草稿")!.trigger("click");
    expect(wrapper.get('[aria-label="草稿版本比较"]').text()).toContain("编辑内容一致");
    mock.store.selectedTourist = { id: 2 };
    await flushPromises();
    expect(wrapper.find('[aria-label="草稿版本比较"]').exists()).toBe(false);
    await wrapper.findAll("button").find((button) => button.text() === "比较草稿")!.trigger("click");
    expect((wrapper.get('[aria-label="比较基准草稿"]').element as HTMLSelectElement).value).toBe("2");
    expect(wrapper.get('[aria-label="草稿版本比较"]').text()).toContain("当前 #2");
    mock.store.selectedTourist = { id: 1 };
    await flushPromises();
    expect(wrapper.find('[aria-label="草稿版本比较"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
