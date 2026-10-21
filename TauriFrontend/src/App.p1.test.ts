import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.vue";
import { projectsApi } from "./lib/api/projects";
import { systemApi } from "./lib/api/system";
import { useWorkbenchStore } from "./stores/workbench";


function readyResponse(): Response {
  return new Response(JSON.stringify({
    status: "degraded",
    queue_mode: "local",
    queue_ready: true,
    clip_loaded: true,
  }), { status: 200, headers: { "content-type": "application/json" } });
}


describe("P1 core workbench behavior", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(readyResponse()));
  });

  it("shows image actions without video-only wording", async () => {
    const wrapper = mount(App);
    const store = useWorkbenchStore();
    store.coreProjectId = 1;
    store.selectedResult = {
      id: 11,
      path: "/objects/result.jpg",
      similarity: 0.91,
      source_info: "photo",
      media_filename: "visitor.jpg",
      media_type: "image/jpeg",
      original_media_url: "/api/v1/projects/1/media/8/content",
    };
    await nextTick();
    await wrapper.get('button[aria-label="打开检索结果"]').trigger("click");

    expect(wrapper.text()).toContain("查看图片");
    expect(wrapper.text()).toContain("查看原图");
    expect(wrapper.text()).toContain("下载原图");
    expect(wrapper.text()).not.toContain("查看视频帧位置");
    expect(wrapper.text()).not.toContain("下载完整视频");
    wrapper.unmount();
  });

  it("keeps video positioning actions for video results", async () => {
    const wrapper = mount(App);
    const store = useWorkbenchStore();
    store.coreProjectId = 1;
    store.selectedResult = {
      id: 12,
      path: "/objects/frame.jpg",
      similarity: 0.88,
      source_info: "camera",
      media_filename: "camera.mp4",
      media_type: "video/mp4",
      original_media_url: "/api/v1/projects/1/media/9/content",
    };
    await nextTick();
    await wrapper.get('button[aria-label="打开检索结果"]').trigger("click");

    expect(wrapper.text()).toContain("查看命中帧");
    expect(wrapper.text()).toContain("查看视频帧位置");
    expect(wrapper.text()).toContain("查看完整视频");
    expect(wrapper.text()).toContain("下载完整视频");
    wrapper.unmount();
  });

  it("guides users without a project and disables project-dependent actions", async () => {
    const wrapper = mount(App, { attachTo: document.body });
    const store = useWorkbenchStore();
    store.coreAccessToken = "core-access";
    store.coreUser = { username: "operator", role: "operator" } as typeof store.coreUser;
    store.coreProjectId = 0;
    await nextTick();

    expect(wrapper.text()).toContain("请先创建或选择项目");
    expect(wrapper.get('button[aria-label="重建当前项目索引"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('button[title="请先创建或选择项目"]')).toBeDefined();

    const iconOnlyButtons = wrapper.findAll("button").filter((button) => !button.text().trim());
    expect(iconOnlyButtons.length).toBeGreaterThan(0);
    expect(iconOnlyButtons.every((button) => Boolean(button.attributes("aria-label")))).toBe(true);
    wrapper.unmount();
  });

  it("restores focus to the icon that opened a modal", async () => {
    const wrapper = mount(App, { attachTo: document.body });
    const trigger = wrapper.get('button[aria-label="打开登录窗口"]');
    (trigger.element as HTMLButtonElement).focus();
    await trigger.trigger("click");
    await nextTick();
    await nextTick();

    const dialog = document.body.querySelector<HTMLElement>('[aria-labelledby="core-account-dialog-title"]');
    expect(dialog?.contains(document.activeElement)).toBe(true);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await nextTick();
    await nextTick();
    expect(document.activeElement).toBe(trigger.element);
    wrapper.unmount();
  });

  it("routes ordinary users to project audit and admins to global observability", async () => {
    const store = useWorkbenchStore();
    const projectAudit = vi.spyOn(projectsApi, "audit").mockResolvedValue([{ id: 1, action: "media.uploaded" }]);
    const globalAudit = vi.spyOn(systemApi, "observability").mockResolvedValue({ logs: [{ event: "system.ready" }] });

    store.coreUser = { username: "operator", role: "operator" } as typeof store.coreUser;
    store.coreProjectId = 42;
    await store.loadAuditLogs("core");
    expect(projectAudit).toHaveBeenCalledWith("core", 42, expect.any(AbortSignal));
    expect(globalAudit).not.toHaveBeenCalled();
    expect(store.auditRows[0]?.action).toBe("media.uploaded");

    store.coreUser = { username: "admin", role: "admin" } as typeof store.coreUser;
    await store.loadAuditLogs("core");
    expect(globalAudit).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(store.auditRows[0]?.event).toBe("system.ready");
  });

  it("updates unified camera frames only after a replacement is ready", async () => {
    const wrapper = mount(App);
    const store = useWorkbenchStore();
    store.coreProjects = [{
      id: 7,
      name: "实时项目",
      description: "",
      project_type: "camera",
      camera_source: "3",
      owner_username: "operator",
      created_at: "2026-09-02",
    }];
    store.coreProjectId = 7;
    store.coreCameraStatus = {
      running: true,
      latest_frame_url: "/gallery/project_7/camera/latest.jpg",
      latest_frame_version: 1,
      monitor_status: {
        latest_frame_url: "/gallery/central_camera/3/latest.jpg",
        latest_frame_version: 19,
      },
    };
    await nextTick();

    await wrapper.get('img[src="/gallery/central_camera/3/latest.jpg?v=19"]').trigger("load");
    const imageNodes = wrapper.findAll(".live-camera-preview img").map((image) => image.element);
    expect(wrapper.get('img[alt="摄像头实时预览"]').attributes("src")).toBe("/gallery/central_camera/3/latest.jpg?v=19");
    for (const version of [20, 21]) {
      store.coreCameraStatus = {
        ...store.coreCameraStatus,
        monitor_status: { ...store.coreCameraStatus?.monitor_status, latest_frame_version: version },
      };
      await nextTick();
      expect(wrapper.get('img[alt="摄像头实时预览"]').attributes("src")).toBe(`/gallery/central_camera/3/latest.jpg?v=${version - 1}`);
      await wrapper.get(`img[src="/gallery/central_camera/3/latest.jpg?v=${version}"]`).trigger("load");
      expect(wrapper.findAll(".live-camera-preview img").map((image) => image.element)).toEqual(imageNodes);
      expect(wrapper.get('img[alt="摄像头实时预览"]').attributes("src")).toBe(`/gallery/central_camera/3/latest.jpg?v=${version}`);
    }
    wrapper.unmount();
  });

  it("opens the dedicated results view after a successful text search", async () => {
    const wrapper = mount(App);
    const store = useWorkbenchStore();
    store.coreProjectId = 7;
    vi.spyOn(store, "runTextSearch").mockImplementation(async (queryText) => {
      store.searchResponse = {
        status: "success",
        task_id: 31,
        task: { id: 31, type: "text", content: queryText, filter_summary: "全部媒体" },
        results: [{ id: 4, rank: 1, path: "/objects/hit.jpg", similarity: 0.94, source_info: "photo" }],
      };
      store.searchResults = store.searchResponse.results;
      store.selectedResult = store.searchResults[0];
    });

    await wrapper.get('textarea[placeholder="描述目标人物的衣着、外貌等特征"]').setValue("穿蓝色外套的人");
    await wrapper.get('button[title="执行文本检索"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("本次检索内容");
    expect(wrapper.text()).toContain("穿蓝色外套的人");
    expect(wrapper.text()).toContain("命中结果");
    wrapper.unmount();
  });
});
