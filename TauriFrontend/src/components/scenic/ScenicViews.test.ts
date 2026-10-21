import { nextTick, reactive } from "vue";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ScenicAuditView from "./ScenicAuditView.vue";
import ScenicHistoryView from "./ScenicHistoryView.vue";
import ScenicMatchingView from "./ScenicMatchingView.vue";
import ScenicMonitorView from "./ScenicMonitorView.vue";
import ScenicRegistrationView from "./ScenicRegistrationView.vue";
import ScenicVlogView from "./ScenicVlogView.vue";

// Stream decoding/reconnect is covered by LiveCameraPreview and browser tests;
// these view tests exercise camera selection, capture and registration state.
vi.mock("../LiveCameraPreview.vue", () => ({ default: {
  props: ["src", "alt", "streamEndpoint", "authScope"], template: '<img :src="src" :alt="alt" />',
} }));


const mockStore = vi.hoisted(() => ({
  tourists: [] as Record<string, unknown>[],
  touristQuery: { q: "", page: 1, page_size: 20, status: "", work_queue: "", date_from: "", date_to: "" },
  touristPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
  touristQueueCounts: {}, touristLoading: false, touristError: "",
  scenicHistoryQuery: { q: "", page: 1, page_size: 20, query_type: "", date_from: "", date_to: "" },
  scenicHistoryPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
  scenicHistoryLoading: false, scenicHistoryError: "",
  scenicAuditQuery: { q: "", page: 1, page_size: 20, action: "", date_from: "", date_to: "" },
  scenicAuditPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
  scenicAuditLoading: false, scenicAuditError: "",
  loadScenicTourists: vi.fn(), loadSearchHistory: vi.fn(),
  selectedTourist: null as Record<string, unknown> | null,
  searchHistory: [] as Record<string, unknown>[],
  auditRows: [] as Record<string, unknown>[],
  scenicCameraWall: [] as Record<string, unknown>[],
  scenicCameraPoints: [] as Record<string, unknown>[],
  scenicCameraDevices: [] as Record<string, unknown>[],
  scenicMediaItems: [] as Record<string, unknown>[],
  scenicProjectStatus: null as Record<string, unknown> | null,
  jobs: [] as Record<string, unknown>[],
  scenicProjectId: 1,
  scenicUser: null as Record<string, unknown> | null,
  error: "",
  loadAuditLogs: vi.fn(),
  refreshWorkspace: vi.fn(),
  captureScenicSnapshot: vi.fn(),
  prepareScenicCameraPreview: vi.fn(),
  releaseScenicCameraPreview: vi.fn(),
  registerTourist: vi.fn(),
  selectTourist: vi.fn(),
  matchSelectedTourist: vi.fn(),
  updateSegment: vi.fn(),
  updateSegments: vi.fn(),
  generateVlog: vi.fn(),
  syncScenicCameraPoints: vi.fn(),
  toggleScenicCameraWall: vi.fn(),
  createScenicCameraPoint: vi.fn(),
  refreshSystem: vi.fn(),
  setScenicCameraEnabled: vi.fn(),
  uploadScenicSimulationVideo: vi.fn(),
}));

vi.mock("../../stores/workbench", () => ({
  useWorkbenchStore: () => mockStore,
}));


describe("scenic focused views", () => {
  beforeEach(() => {
    mockStore.tourists = [];
    mockStore.selectedTourist = null;
    mockStore.searchHistory = [];
    mockStore.auditRows = [];
    mockStore.scenicCameraWall = [];
    mockStore.scenicCameraPoints = [];
    mockStore.scenicCameraDevices = [];
    mockStore.scenicMediaItems = [];
    mockStore.scenicProjectStatus = null;
    mockStore.jobs = [];
    mockStore.error = "";
    mockStore.loadAuditLogs.mockReset();
    mockStore.refreshWorkspace.mockReset();
    mockStore.captureScenicSnapshot.mockReset();
    mockStore.prepareScenicCameraPreview.mockReset();
    mockStore.releaseScenicCameraPreview.mockReset();
    mockStore.registerTourist.mockReset();
    mockStore.selectTourist.mockReset();
    mockStore.matchSelectedTourist.mockReset();
    mockStore.updateSegment.mockReset();
    mockStore.updateSegments.mockReset();
    mockStore.generateVlog.mockReset();
    mockStore.syncScenicCameraPoints.mockReset();
    mockStore.toggleScenicCameraWall.mockReset();
    mockStore.createScenicCameraPoint.mockReset();
    mockStore.refreshSystem.mockReset();
    mockStore.setScenicCameraEnabled.mockReset();
    mockStore.uploadScenicSimulationVideo.mockReset();
  });

  it("renders visitor history and emits focused navigation and preview events", async () => {
    mockStore.tourists = [{
      id: 7,
      ticket_code: "T-007",
      visitor_label: "测试游客",
      status: "matched",
      created_at: "2026-08-31T09:30:00Z",
    }];
    mockStore.searchHistory = [{
      id: 3,
      content: "出园匹配",
      result_count: 1,
      created_at: "2026-08-31T10:00:00Z",
      results: [{ id: 21, rank: 1, similarity: 0.934, thumbnail_url: "/objects/frame.jpg" }],
    }];

    const wrapper = mount(ScenicHistoryView);

    expect(wrapper.text()).toContain("测试游客");
    expect(wrapper.text()).toContain("已匹配");
    expect(wrapper.text()).toContain("出园匹配");
    expect(wrapper.text()).toContain("93.4%");

    await wrapper.get(".tourist-row").trigger("click");
    expect(wrapper.emitted("select-tourist")).toEqual([[7]]);

    await wrapper.get(".thumb-strip button").trigger("click");
    expect(wrapper.emitted("preview")?.[0]?.[0]).toMatchObject({
      title: "历史命中 #1",
      source: "/objects/frame.jpg",
      mediaType: "image",
    });
  });

  it("renders scenic audit rows and refreshes only the scenic scope", async () => {
    mockStore.auditRows = [{
      id: 9,
      action: "tourist.register",
      username: "operator",
      resource_type: "tourist",
      resource_id: "7",
      created_at: "2026-08-31T09:30:00Z",
      detail: "consent=true",
    }];

    const wrapper = mount(ScenicAuditView);

    expect(wrapper.text()).toContain("tourist.register");
    expect(wrapper.text()).toContain("operator");
    expect(wrapper.text()).toContain("consent=true");

    await wrapper.get(".refresh-button").trigger("click");
    expect(mockStore.loadAuditLogs).toHaveBeenCalledOnce();
    expect(mockStore.loadAuditLogs).toHaveBeenCalledWith("scenic");
  });

  it("owns tourist registration state and emits visitor selection", async () => {
    mockStore.scenicCameraWall = [{ running: true, role: "entry", point_id: 5, latest_frame_url: "/gallery/entry.jpg" }];
    mockStore.tourists = [{ id: 7, ticket_code: "T-007", status: "registered" }];
    mockStore.captureScenicSnapshot.mockResolvedValue({
      snapshot_id: "snapshot-1",
      snapshot_url: "/objects/snapshot.jpg",
    });

    const wrapper = mount(ScenicRegistrationView);
    await wrapper.get("button.scenic-button.primary:not(.submit-button)").trigger("click");
    await wrapper.get('input[aria-label="门票号"]').setValue("T-008");
    await wrapper.get('input[aria-label="游客备注"]').setValue("亲子游客");
    expect(wrapper.get(".submit-button").attributes("disabled")).toBeDefined();
    await wrapper.findAll('input[type="checkbox"]')[2].setValue(true);
    await wrapper.get(".submit-button").trigger("click");

    expect(mockStore.captureScenicSnapshot).toHaveBeenCalledOnce();
    expect(mockStore.captureScenicSnapshot).toHaveBeenCalledWith(5);
    expect(mockStore.registerTourist).toHaveBeenCalledWith(
      expect.objectContaining({ snapshot_id: "snapshot-1" }),
      "T-008",
      "亲子游客",
      "",
      true,
    );

    await wrapper.get(".tourist-row").trigger("click");
    expect(wrapper.emitted("select-tourist")).toEqual([[7]]);
  });


  it("uses backend camera ids, allows non-entry cameras and preserves signed frame queries", async () => {
    mockStore.scenicCameraWall = [
      { id: 11, name: "入口", role: "entry", monitor_running: true, latest_frame_url: "/gallery/entry.jpg" },
      { id: 12, name: "园内", role: "play", monitor_running: true, latest_frame_url: "/gallery/play.jpg?signature=valid", latest_frame_version: 9 },
      { id: 13, name: "已停用", role: "exit", is_active: false },
    ];
    mockStore.captureScenicSnapshot.mockResolvedValue({ snapshot_url: "/objects/capture.jpg" });
    const wrapper = mount(ScenicRegistrationView);
    const select = wrapper.get('select[aria-label="入口抓拍相机"]');
    expect(select.findAll("option").map((option) => option.attributes("value"))).toEqual(["11", "12"]);
    expect(mockStore.prepareScenicCameraPreview).toHaveBeenCalledWith(11);
    await select.setValue("12");
    expect(mockStore.prepareScenicCameraPreview).toHaveBeenLastCalledWith(12);
    const preview = new URL(wrapper.get(".camera-preview img").attributes("src") || "");
    expect(preview.pathname).toBe("/gallery/play.jpg");
    expect(preview.searchParams.get("signature")).toBe("valid");
    expect(preview.searchParams.get("v")).toBe("9");
    await wrapper.get("button.scenic-button.primary:not(.submit-button)").trigger("click");
    expect(mockStore.captureScenicSnapshot).toHaveBeenCalledWith(12);
    expect(wrapper.get(".entry-drop img").attributes("src")).toContain("/objects/capture.jpg");
    wrapper.unmount();
    expect(mockStore.releaseScenicCameraPreview).toHaveBeenCalledOnce();
  });

  it("keeps the chosen camera through frame refreshes and falls back when it is disabled", async () => {
    const cameras = reactive([
      { id: 11, name: "入口", role: "entry", is_active: true, monitor_running: true, latest_frame_url: "/gallery/entry.jpg", latest_frame_version: 1 },
      { id: 12, name: "园内", role: "play", is_active: true, monitor_running: true, latest_frame_url: "/gallery/play.jpg", latest_frame_version: 1 },
    ]);
    mockStore.scenicCameraWall = cameras;
    const wrapper = mount(ScenicRegistrationView);
    const select = wrapper.get('select[aria-label="入口抓拍相机"]');
    await select.setValue("12");
    cameras.splice(1, 1, { ...cameras[1], latest_frame_version: 2 });
    await nextTick();
    expect((select.element as HTMLSelectElement).value).toBe("12");
    expect(wrapper.get(".camera-preview img").attributes("src")).toContain("v=2");
    expect(mockStore.prepareScenicCameraPreview).toHaveBeenCalledTimes(2);
    cameras[1].is_active = false;
    await nextTick();
    expect((select.element as HTMLSelectElement).value).toBe("11");
    expect(mockStore.prepareScenicCameraPreview).toHaveBeenLastCalledWith(11);
    wrapper.unmount();
  });

  it("waits for the selected camera's first frame before enabling capture", async () => {
    const cameras = reactive([{ id: 11, role: "entry", monitor_running: true, latest_frame_url: "" }]);
    mockStore.scenicCameraWall = cameras;
    const wrapper = mount(ScenicRegistrationView);
    const button = wrapper.get("button.scenic-button.primary:not(.submit-button)");
    expect(button.attributes("disabled")).toBeDefined();
    expect(wrapper.get(".camera-preview").text()).toBe("等待实时画面");
    cameras[0].latest_frame_url = "/gallery/entry.jpg";
    await nextTick();
    expect(button.attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("matches a visitor and correctly toggles default-included segments off", async () => {
    mockStore.tourists = [{ id: 7, ticket_code: "T-007", status: "matched" }];
    mockStore.selectedTourist = {
      id: 7,
      ticket_code: "T-007",
      status: "matched",
      segments: [{
        id: 33,
        camera_name: "入口",
        similarity: 0.725,
        thumbnail_url: "/objects/segment.jpg",
        start_seconds: 2,
        end_seconds: 7,
      }],
    };

    const wrapper = mount(ScenicMatchingView);
    expect(wrapper.text()).toContain("72.5%");

    await wrapper.get(".threshold-box input").setValue("0.35");
    const matchButton = wrapper.findAll("button").find((button) => button.text().includes("开始匹配"));
    await matchButton?.trigger("click");
    expect(mockStore.matchSelectedTourist).toHaveBeenCalledWith(0.35);

    await wrapper.get('.segment-actions input[type="checkbox"]').trigger("change");
    expect(mockStore.updateSegment).toHaveBeenCalledWith(33, false, 0);

    await wrapper.get(".segment-thumb").trigger("click");
    expect(wrapper.emitted("preview")?.[0]?.[0]).toMatchObject({
      source: "/objects/segment.jpg",
      mediaType: "image",
    });
  });

  it("renders vlog delivery state and delegates generation and preview", async () => {
    mockStore.selectedTourist = {
      id: 7,
      ticket_code: "T-007",
      visitor_label: "测试游客",
      segments: [{ id: 33, camera_name: "入口", included: true }],
      vlog_jobs: [{
        id: 44,
        status: "succeeded",
        segment_count: 1,
        duration_seconds: 8,
        preview_url: "/objects/vlog-preview.mp4",
        full_url: "/objects/vlog-full.mp4",
      }],
      orders: [],
    };

    const wrapper = mount(ScenicVlogView);
    const generateButton = wrapper.findAll("button").find((button) => button.text().includes("生成视频短片"));
    await generateButton?.trigger("click");
    expect(mockStore.generateVlog).toHaveBeenCalledOnce();

    const previewButton = wrapper.findAll("button").find((button) => button.text() === "预览");
    await previewButton?.trigger("click");
    expect(wrapper.emitted("preview")?.[0]?.[0]).toMatchObject({
      title: "测试游客 预览成片",
      source: "/objects/vlog-preview.mp4",
      mediaType: "video",
    });
  });

  it("shows current camera ingestion and server counts despite historical failed jobs", () => {
    mockStore.scenicProjectStatus = { indexed_targets: 621, media_total: 10, indexed_media: 2, failed_media: 3 };
    mockStore.scenicMediaItems = [{ id: 1, target_count: 563, status: "indexed" }];
    mockStore.jobs = [
      { id: "live", type: "camera_stream", status: "running", progress: .5, message: "实时入库中，已入库 13 个目标" },
      { id: "old", type: "camera_stream", status: "failed", error: "历史任务已终止" },
    ];
    const wrapper = mount(ScenicMonitorView);
    expect(wrapper.get(".metric-row").text()).toContain("621");
    expect(wrapper.get(".metric-row").text()).toContain("实时入库中");
    expect(wrapper.get(".library-mini").text()).toContain("已入库 13 个目标");
    expect(wrapper.get(".metric-row").text()).not.toContain("入库异常");
    wrapper.unmount();
  });

  it("keeps completed targets usable while retaining failed task details", () => {
    mockStore.scenicProjectStatus = { indexed_targets: 621, indexed_media: 2, failed_media: 1 };
    mockStore.jobs = [{ id: "old", type: "camera_stream", status: "failed", error: "历史任务已终止" }];
    const wrapper = mount(ScenicMonitorView);
    expect(wrapper.get(".metric-row").text()).toContain("可匹配");
    expect(wrapper.get(".metric-row").text()).toContain("已入库 621 个目标");
    expect(wrapper.get(".simulator-panel .compact-list").text()).toContain("历史任务已终止");
    wrapper.unmount();
  });

  it("still exposes an error from the currently running ingestion task", () => {
    mockStore.scenicProjectStatus = { indexed_targets: 621 };
    mockStore.jobs = [{ id: "live", type: "camera_stream", status: "running", error: "向量服务不可用" }];
    const wrapper = mount(ScenicMonitorView);
    expect(wrapper.get(".metric-row").text()).toContain("入库异常");
    expect(wrapper.get(".library-mini").text()).toContain("向量服务不可用");
    wrapper.unmount();
  });

  it("allows ingestion to restart while a camera is only previewing", async () => {
    mockStore.scenicCameraPoints = [{ id: 1, is_active: true }];
    mockStore.scenicCameraWall = [{ point_id: 1, running: false, monitor_running: true }];
    const wrapper = mount(ScenicMonitorView);
    const start = wrapper.findAll("button").find(button => button.text() === "启动监控墙")!;
    const stop = wrapper.findAll("button").find(button => button.text() === "停止监控墙")!;
    expect(start.attributes("disabled")).toBeUndefined();
    expect(stop.attributes("disabled")).toBeDefined();
    await start.trigger("click");
    expect(mockStore.toggleScenicCameraWall).toHaveBeenCalledWith(true);
    wrapper.unmount();
  });

  it("owns camera monitor controls and manual point creation", async () => {
    mockStore.scenicCameraDevices = [{ source: "0", label: "本机相机" }];
    const wrapper = mount(ScenicMonitorView);
    expect(wrapper.text()).toContain("暂无摄像头点位");
    expect(wrapper.text()).toContain("等待入库");

    await wrapper.get('input[aria-label="点位名称"]').setValue("出口点位");
    await wrapper.get('input[aria-label="摄像头来源"]').setValue("exit-camera");
    const createButton = wrapper.findAll("button").find((button) => button.text().includes("新增点位"));
    await createButton?.trigger("click");

    expect(mockStore.createScenicCameraPoint).toHaveBeenCalledWith(
      "出口点位",
      "exit-camera",
      "play",
      "",
    );
  });
});
