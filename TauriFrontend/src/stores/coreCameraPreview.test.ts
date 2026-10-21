import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mediaApi } from "../lib/api/media";
import { projectsApi, type CameraStatus } from "../lib/api/projects";
import { useWorkbenchStore } from "./workbench";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function fixture() {
  const store = useWorkbenchStore();
  store.coreAccessToken = "token";
  store.coreProjectId = 7;
  store.coreProjects = [{ id: 7, name: "Camera", description: "", project_type: "camera", owner_username: "operator", created_at: "" }];
  vi.spyOn(projectsApi, "cameraStatus").mockResolvedValue({ running: true, latest_frame_version: 2 });
  vi.spyOn(projectsApi, "status").mockResolvedValue({ status: "ready" });
  vi.spyOn(projectsApi, "preview").mockResolvedValue({ previews: [] });
  vi.spyOn(projectsApi, "jobs").mockResolvedValue([]);
  vi.spyOn(mediaApi, "list").mockResolvedValue([]);
  return store;
}

describe("core camera live frame polling", () => {
  beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()); });

  it("refreshes live frames even when optional activity refresh is disabled", async () => {
    const store = fixture();
    store.updatePreferences({ autoRefresh: false });
    await store.pollLiveFeeds();
    expect(projectsApi.cameraStatus).toHaveBeenCalledWith(7, true, expect.any(AbortSignal));
    expect(store.coreCameraStatus?.latest_frame_version).toBe(2);
    expect(projectsApi.status).not.toHaveBeenCalled();
  });

  it("continues polling frames while project statistics are blocked", async () => {
    const store = fixture();
    const blocked = deferred<Awaited<ReturnType<typeof projectsApi.status>>>();
    vi.mocked(projectsApi.status).mockReturnValueOnce(blocked.promise);
    const first = store.pollLiveFeeds();
    await flushPromises();
    vi.mocked(projectsApi.cameraStatus).mockResolvedValue({ running: true, latest_frame_version: 3 });
    await store.pollLiveFeeds();
    expect(store.coreCameraStatus?.latest_frame_version).toBe(3);
    expect(projectsApi.status).toHaveBeenCalledOnce();
    blocked.resolve({ status: "ready" });
    await first;
  });

  it("keeps an in-flight frame request alive across timer ticks", async () => {
    const store = fixture();
    store.updatePreferences({ autoRefresh: false });
    const frame = deferred<CameraStatus>();
    vi.mocked(projectsApi.cameraStatus).mockReturnValueOnce(frame.promise);
    const first = store.pollLiveFeeds();
    await store.pollLiveFeeds();
    expect(projectsApi.cameraStatus).toHaveBeenCalledOnce();
    expect(vi.mocked(projectsApi.cameraStatus).mock.calls[0][2]?.aborted).toBe(false);
    frame.resolve({ running: true, latest_frame_version: 3 });
    await first;
    expect(store.coreCameraStatus?.latest_frame_version).toBe(3);
  });

  it("does not replace a live frame with an older workspace response", async () => {
    const store = fixture();
    const oldFrame = deferred<CameraStatus>();
    vi.mocked(projectsApi.cameraStatus).mockReturnValueOnce(oldFrame.promise);
    const refreshing = store.refreshWorkspace("core");
    await store.pollLiveFeeds();
    oldFrame.resolve({ running: true, latest_frame_version: 1 });
    await refreshing;
    expect(store.coreCameraStatus?.latest_frame_version).toBe(2);
  });
});
