import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api/http";
import { mediaApi } from "../lib/api/media";
import { projectsApi } from "../lib/api/projects";
import { scenicApi, type ScenicCameraWallItem } from "../lib/api/scenic";
import { useWorkbenchStore } from "./workbench";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function camera(id = 11, version = 1): ScenicCameraWallItem {
  return { id, role: "entry", monitor_running: true, latest_frame_url: `/gallery/${id}.jpg`, latest_frame_version: version };
}

function wall(version = 1) {
  return { status: "ok", camera_wall: [camera(11, version)], active_count: 1, running_count: 0 };
}

function fixture() {
  const store = useWorkbenchStore();
  store.mode = "scenic";
  store.scenicProjectId = 7;
  store.scenicAccessToken = "token";
  vi.spyOn(scenicApi, "discoverCameraPoints").mockResolvedValue({ status: "ok", created: 0, camera_points: [] });
  vi.spyOn(scenicApi, "startCameraWall").mockResolvedValue({ status: "ok" });
  vi.spyOn(scenicApi, "stopCameraWall").mockResolvedValue({ status: "ok" });
  vi.spyOn(scenicApi, "dashboard").mockResolvedValue({ status: "ok", project_id: 7 });
  vi.spyOn(scenicApi, "cameraPoints").mockResolvedValue({ status: "ok", camera_points: [] });
  vi.spyOn(scenicApi, "cameraWall").mockResolvedValue(wall());
  vi.spyOn(scenicApi, "prepareCameraPreview").mockImplementation(async (_project, point) => ({ status: "ok", camera: camera(point) }));
  vi.spyOn(scenicApi, "tourists").mockResolvedValue({ tourists: [] });
  vi.spyOn(scenicApi, "audit").mockResolvedValue({ audit: [] });
  vi.spyOn(scenicApi, "history").mockResolvedValue({ history: [] });
  vi.spyOn(projectsApi, "status").mockResolvedValue({ status: "ready" });
  vi.spyOn(projectsApi, "preview").mockResolvedValue({ previews: [] });
  vi.spyOn(projectsApi, "jobs").mockResolvedValue([]);
  vi.spyOn(mediaApi, "list").mockResolvedValue([]);
  return store;
}

describe("scenic registration camera lifecycle", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("myreid.scenic.access", "token");
    setActivePinia(createPinia());
  });
  afterEach(() => vi.restoreAllMocks());

  it("initializes every enabled camera once per entry and respects a manual stop", async () => {
    const store = fixture();
    store.mode = "core";
    vi.mocked(scenicApi.cameraPoints).mockResolvedValue({ status: "ok", camera_points: [
      { id: 11, name: "Entry", role: "entry", camera_source: "0", is_active: true },
      { id: 12, name: "Lake", role: "play", camera_source: "3", is_active: true },
    ] });
    store.setMode("scenic");
    await flushPromises();
    expect(scenicApi.discoverCameraPoints).not.toHaveBeenCalled();
    expect(scenicApi.startCameraWall).toHaveBeenCalledWith(7, "0", expect.any(AbortSignal));
    await store.refreshWorkspace("scenic");
    expect(scenicApi.startCameraWall).toHaveBeenCalledOnce();
    await store.toggleScenicCameraWall(false);
    expect(scenicApi.stopCameraWall).toHaveBeenCalledOnce();
    expect(scenicApi.startCameraWall).toHaveBeenCalledOnce();
    store.setMode("core");
    store.setMode("scenic");
    await flushPromises();
    expect(scenicApi.startCameraWall).toHaveBeenCalledTimes(2);
  });

  it("starts configured points without scanning physical devices", async () => {
    const store = fixture();
    vi.mocked(scenicApi.discoverCameraPoints).mockRejectedValue(new ApiError(403, "Manage permission required", null));
    vi.mocked(scenicApi.cameraPoints).mockResolvedValue({ status: "ok", camera_points: [
      { id: 11, name: "Entry", role: "entry", camera_source: "0", is_active: true },
    ] });
    await store.refreshWorkspace("scenic");
    expect(scenicApi.startCameraWall).toHaveBeenCalledOnce();
    expect(store.error).toBe("");
  });

  it("reports an initialization failure and retries on explicit refresh", async () => {
    const store = fixture();
    vi.mocked(scenicApi.startCameraWall).mockRejectedValueOnce(new ApiError(503, "相机正在释放", null));
    vi.mocked(scenicApi.cameraPoints).mockResolvedValue({ status: "ok", camera_points: [
      { id: 11, name: "Entry", role: "entry", camera_source: "0", is_active: true },
    ] });
    await store.refreshWorkspace("scenic");
    expect(store.error).toBe("相机正在释放");
    await store.refreshWorkspace("scenic");
    expect(scenicApi.startCameraWall).toHaveBeenCalledTimes(2);
    expect(store.error).toBe("");
  });

  it("does not start cameras after leaving while loading points", async () => {
    const store = fixture();
    const discovery = deferred<Awaited<ReturnType<typeof scenicApi.cameraPoints>>>();
    vi.mocked(scenicApi.cameraPoints).mockReturnValue(discovery.promise);
    const refreshing = store.refreshWorkspace("scenic");
    store.setMode("core");
    discovery.resolve({ status: "ok", camera_points: [
      { id: 11, name: "Entry", role: "entry", camera_source: "0", is_active: true },
    ] });
    await refreshing;
    expect(scenicApi.startCameraWall).not.toHaveBeenCalled();
  });

  it("does not restart disabled camera points", async () => {
    const store = fixture();
    vi.mocked(scenicApi.cameraPoints).mockResolvedValue({ status: "ok", camera_points: [{ id: 11, name: "Disabled", role: "entry", camera_source: "0", is_active: false }] });
    await store.refreshWorkspace("scenic");
    expect(scenicApi.startCameraWall).not.toHaveBeenCalled();
  });

  it("loads camera choices when switching systems before slow statistics finish", async () => {
    const store = fixture();
    store.mode = "core";
    const status = deferred<Awaited<ReturnType<typeof projectsApi.status>>>();
    const dashboard = deferred<Awaited<ReturnType<typeof scenicApi.dashboard>>>();
    vi.mocked(projectsApi.status).mockReturnValue(status.promise);
    vi.mocked(scenicApi.dashboard).mockReturnValue(dashboard.promise);
    store.setMode("scenic");
    await flushPromises();
    expect(store.scenicCameraWall).toEqual([camera()]);
    expect(store.scenicProjectStatus).toBeNull();
    status.resolve({ status: "ready" });
    dashboard.resolve({ status: "ok", project_id: 7 });
    await flushPromises();
  });

  it("does not overwrite a newer live frame with an older workspace response", async () => {
    const store = fixture();
    store.updatePreferences({ autoRefresh: true });
    const stale = deferred<ReturnType<typeof wall>>();
    vi.mocked(scenicApi.cameraWall).mockReturnValueOnce(stale.promise).mockResolvedValue(wall(2));
    const refresh = store.refreshWorkspace("scenic");
    await store.pollLiveFeeds();
    stale.resolve(wall(1));
    await refresh;
    expect(store.scenicCameraWall[0].latest_frame_version).toBe(2);
  });

  it("renews the selected camera and refreshes frames while activity polling is blocked", async () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(100_000);
    const store = fixture();
    store.updatePreferences({ autoRefresh: true });
    await store.prepareScenicCameraPreview(11);
    const blocked = deferred<Awaited<ReturnType<typeof projectsApi.status>>>();
    vi.mocked(projectsApi.status).mockReturnValueOnce(blocked.promise);
    const firstPoll = store.pollLiveFeeds();
    await flushPromises();
    expect(projectsApi.status).toHaveBeenCalledOnce();
    clock.mockReturnValue(106_000);
    await store.pollLiveFeeds();
    expect(scenicApi.prepareCameraPreview).toHaveBeenCalledTimes(2);
    expect(scenicApi.cameraWall).toHaveBeenCalledTimes(2);
    expect(projectsApi.status).toHaveBeenCalledOnce();
    blocked.resolve({ status: "ready" });
    await firstPoll;
  });

  it("renews a preview with auto-refresh disabled even if an earlier wall request is slow", async () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(100_000);
    const store = fixture();
    store.updatePreferences({ autoRefresh: false });
    await store.prepareScenicCameraPreview(11);
    const blocked = deferred<ReturnType<typeof wall>>();
    vi.mocked(scenicApi.cameraWall).mockReturnValueOnce(blocked.promise);
    const firstPoll = store.pollLiveFeeds();
    await flushPromises();
    clock.mockReturnValue(106_000);
    await store.pollLiveFeeds();
    expect(scenicApi.prepareCameraPreview).toHaveBeenCalledTimes(2);
    expect(scenicApi.cameraWall).toHaveBeenCalledOnce();
    expect(projectsApi.status).not.toHaveBeenCalled();
    blocked.resolve(wall());
    await firstPoll;
  });

  it("keeps preview renewal active when the current project is selected again", async () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(100_000);
    const store = fixture();
    store.updatePreferences({ autoRefresh: false });
    await store.prepareScenicCameraPreview(11);
    await store.setProject("scenic", 7);
    clock.mockReturnValue(106_000);
    await store.pollLiveFeeds();
    expect(scenicApi.prepareCameraPreview).toHaveBeenCalledTimes(2);
    expect(scenicApi.prepareCameraPreview).toHaveBeenLastCalledWith(7, 11, expect.any(AbortSignal));
  });

  it("ignores late preview responses and stops renewal after leaving registration", async () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(100_000);
    const store = fixture();
    store.updatePreferences({ autoRefresh: false });
    store.scenicCameraWall = [camera()];
    const late = deferred<Awaited<ReturnType<typeof scenicApi.prepareCameraPreview>>>();
    vi.mocked(scenicApi.prepareCameraPreview).mockReturnValueOnce(late.promise);
    const preparing = store.prepareScenicCameraPreview(12);
    store.releaseScenicCameraPreview();
    late.resolve({ status: "ok", camera: camera(12) });
    await preparing;
    clock.mockReturnValue(106_000);
    await store.pollLiveFeeds();
    expect(store.scenicCameraWall).toEqual([camera()]);
    expect(scenicApi.prepareCameraPreview).toHaveBeenCalledOnce();
    expect(scenicApi.cameraWall).not.toHaveBeenCalled();
  });
});
