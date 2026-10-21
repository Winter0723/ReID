import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { scenicApi, type ScenicTouristList } from "../lib/api/scenic";
import { projectsApi } from "../lib/api/projects";
import { mediaApi } from "../lib/api/media";
import { useWorkbenchStore } from "./workbench";
import { useScenicStore } from "./scenic";

function response(id: number, page = 1): ScenicTouristList {
  return { tourists: [{ id, ticket_code: `T-${id}` }], pagination: { page, page_size: 20, total: 25, total_pages: 2 }, work_queue_counts: { needs_review: 6 } };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function fixture() {
  const store = useWorkbenchStore();
  store.mode = "scenic";
  store.scenicProjectId = 7;
  store.scenicAccessToken = "token";
  vi.spyOn(scenicApi, "dashboard").mockResolvedValue({ status: "ok", project_id: 7 });
  vi.spyOn(scenicApi, "cameraPoints").mockResolvedValue({ status: "ok", camera_points: [] });
  vi.spyOn(scenicApi, "cameraWall").mockResolvedValue({ status: "ok", camera_wall: [], active_count: 0, running_count: 0 });
  vi.spyOn(projectsApi, "status").mockResolvedValue({ status: "ready" });
  vi.spyOn(projectsApi, "preview").mockResolvedValue({ previews: [] });
  vi.spyOn(projectsApi, "jobs").mockResolvedValue([]);
  vi.spyOn(mediaApi, "list").mockResolvedValue([]);
  vi.spyOn(scenicApi, "audit").mockResolvedValue({ audit: [] });
  vi.spyOn(scenicApi, "history").mockResolvedValue({ history: [] });
  return store;
}
describe("scenic server-side work lists", () => {
  beforeEach(() => { localStorage.clear(); localStorage.setItem("myreid.scenic.access", "token"); setActivePinia(createPinia()); });
  it("ignores stale search results and preserves the selected visitor outside the new page", async () => {
    const store = fixture(); const old = deferred<ScenicTouristList>();
    const api = vi.spyOn(scenicApi, "tourists").mockReturnValueOnce(old.promise).mockResolvedValueOnce(response(202));
    store.selectedTourist = { id: 99, ticket_code: "KEEP", segments: [{ id: 9 }] };
    const first = store.loadScenicTourists({ q: "old" });
    await store.loadScenicTourists({ q: "T-202" });
    old.resolve(response(1)); await first;
    expect(store.tourists[0].id).toBe(202);
    expect(store.touristQuery.q).toBe("T-202");
    expect(store.selectedTourist.id).toBe(99);
    expect(api.mock.calls[0][1]?.aborted).toBe(true);
    expect(store.touristLoading).toBe(false);
  });
  it("keeps page and filters in workspace refresh and live polling", async () => {
    const store = fixture();
    const api = vi.spyOn(scenicApi, "tourists").mockResolvedValue(response(21, 2));
    await store.loadScenicTourists({ q: "T-", page: 2, work_queue: "needs_review" });
    await store.refreshWorkspace("scenic");
    store.updatePreferences({ autoRefresh: true });
    await store.pollLiveFeeds();
    expect(api).toHaveBeenCalledTimes(3);
    for (const call of api.mock.calls) expect(call[2]).toMatchObject({ q: "T-", page: 2, work_queue: "needs_review" });
    expect(store.touristPagination.page).toBe(2);
    expect(store.touristQueueCounts.needs_review).toBe(6);
  });
  it("retains existing rows on a fetch failure and supports retry", async () => {
    const store = fixture(); store.tourists = [{ id: 8, ticket_code: "T8" }];
    vi.spyOn(scenicApi, "tourists").mockRejectedValueOnce(new Error("服务暂不可用")).mockResolvedValueOnce(response(9));
    await store.loadScenicTourists();
    expect(store.touristError).toBe("服务暂不可用"); expect(store.tourists[0].id).toBe(8);
    await store.loadScenicTourists();
    expect(store.touristError).toBe(""); expect(store.tourists[0].id).toBe(9);
  });
  it("returns to the last available page when a queue shrinks", async () => {
    const store = fixture();
    const api = vi.spyOn(scenicApi, "tourists").mockResolvedValueOnce({ tourists: [], pagination: { page: 4, page_size: 20, total: 1, total_pages: 1 } }).mockResolvedValueOnce(response(5));
    await store.loadScenicTourists({ page: 4, work_queue: "ready_delivery" });
    expect(api).toHaveBeenCalledTimes(2); expect(store.touristQuery.page).toBe(1); expect(store.tourists[0].id).toBe(5);
  });
  it("keeps a newly registered visitor selected even when the active queue excludes them", async () => {
    const store = fixture();
    vi.spyOn(scenicApi, "tourists").mockResolvedValue(response(21, 2));
    vi.spyOn(scenicApi, "registerTourist").mockResolvedValue({ id: 305, ticket_code: "NEW" });
    await store.loadScenicTourists({ page: 2, work_queue: "ready_delivery" });
    await store.registerTourist(new File(["image"], "visitor.jpg"), "NEW", "新游客", "", true);
    expect(store.selectedTourist?.id).toBe(305); expect(store.touristQuery.page).toBe(2); expect(store.touristQuery.work_queue).toBe("ready_delivery");
  });
  it("scopes history to the current project and preserves audit filters in workspace refresh", async () => {
    const store = fixture(); vi.spyOn(scenicApi, "tourists").mockResolvedValue(response(1));
    store.scenicHistoryQuery = { ...store.scenicHistoryQuery, q: "游客", page: 2 };
    store.scenicAuditQuery = { ...store.scenicAuditQuery, q: "支付", action: "order", page: 3 };
    await store.loadSearchHistory("scenic"); await store.refreshWorkspace("scenic");
    expect(scenicApi.history).toHaveBeenCalledWith(7, expect.objectContaining({ q: "游客", page: 2 }), expect.any(AbortSignal));
    expect(scenicApi.audit).toHaveBeenCalledWith(7, expect.objectContaining({ q: "支付", action: "order", page: 3 }), expect.any(AbortSignal));
  });
  it("clears list filters and pagination when changing projects", async () => {
    const store = fixture(); vi.spyOn(scenicApi, "tourists").mockResolvedValue(response(1));
    store.touristQuery = { ...store.touristQuery, q: "old", page: 2 }; store.selectedTourist = { id: 99, ticket_code: "OLD" };
    await store.setProject("scenic", 8);
    expect(store.touristQuery.q).toBe(""); expect(store.touristQuery.page).toBe(1); expect(store.selectedTourist).toBeNull();
    useScenicStore().resetWorkspace(); expect(store.touristPagination.total).toBe(0);
  });
});
