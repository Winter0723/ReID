import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "./auth";
import { useCoreStore } from "./core";
import { useScenicStore } from "./scenic";
import { useSystemStore } from "./system";
import { useWorkbenchStore } from "./workbench";


describe("focused workbench domain stores", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("keeps core and scenic state isolated", () => {
    const core = useCoreStore();
    const scenic = useScenicStore();

    core.projectId = 11;
    scenic.projectId = 22;
    scenic.tourists = [{ id: 7, ticket_code: "T-007" }] as typeof scenic.tourists;

    expect(core.projectId).toBe(11);
    expect(scenic.projectId).toBe(22);
    expect(core.projects).toEqual([]);
    expect(scenic.tourists).toHaveLength(1);
  });

  it("owns scoped authentication state independently", () => {
    const auth = useAuthStore();
    auth.coreAccessToken = "core-token";
    auth.scenicAccessToken = "scenic-token";
    auth.coreLogin.account = "core-user";

    expect(auth.coreAccessToken).toBe("core-token");
    expect(auth.scenicAccessToken).toBe("scenic-token");
    expect(auth.scenicLogin.account).toBe("");
  });

  it("persists and clamps system preferences", () => {
    const system = useSystemStore();
    system.updatePreferences({ autoRefresh: false, livePollIntervalMs: 50 });

    expect(system.preferences).toEqual({ autoRefresh: false, livePollIntervalMs: 500 });
    expect(JSON.parse(localStorage.getItem("myreid.preferences") || "{}")).toEqual(system.preferences);
  });

  it("keeps the compatibility workbench facade connected to domain state", () => {
    const workbench = useWorkbenchStore();
    const core = useCoreStore();
    const scenic = useScenicStore();

    core.projectId = 31;
    scenic.projectId = 41;
    expect(workbench.coreProjectId).toBe(31);
    expect(workbench.scenicProjectId).toBe(41);

    workbench.scenicProjectId = 42;
    expect(scenic.projectId).toBe(42);
  });

  it("keeps controller and search state in their focused domains", () => {
    const workbench = useWorkbenchStore();
    const core = useCoreStore();
    const system = useSystemStore();

    workbench.mode = "scenic";
    workbench.message = "刷新中";
    workbench.searchResults = [{ id: 9, path: "/result.jpg" }] as typeof workbench.searchResults;

    expect(system.mode).toBe("scenic");
    expect(system.message).toBe("刷新中");
    expect(core.searchResults).toEqual([{ id: 9, path: "/result.jpg" }]);
  });

  it("resets only the requested domain workspace", () => {
    const core = useCoreStore();
    const scenic = useScenicStore();

    core.projectId = 10;
    core.searchResults = [{ id: 1, path: "/core.jpg" }] as typeof core.searchResults;
    scenic.projectId = 20;
    scenic.tourists = [{ id: 2, ticket_code: "SCENIC-2" }] as typeof scenic.tourists;

    core.resetWorkspace();

    expect(core.projectId).toBe(0);
    expect(core.searchResults).toEqual([]);
    expect(scenic.projectId).toBe(20);
    expect(scenic.tourists).toHaveLength(1);
  });
});
