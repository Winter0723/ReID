import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ScenicAccountDialog from "./ScenicAccountDialog.vue";
import ScenicProjectView from "./ScenicProjectView.vue";


const mockStore = vi.hoisted(() => ({
  isAuthenticated: true,
  error: "",
  scenicProjectId: 2,
  scenicProjects: [{ id: 2, name: "西湖景区", project_type: "scenic", created_at: "2026-09-01" }],
  scenicUser: {
    username: "operator",
    email: "operator@example.com",
    public_id: "scenic-user",
    role: "operator",
    is_active: true,
    created_at: "2026-09-01",
  },
  preferences: { autoRefresh: true, livePollIntervalMs: 1000 },
  createProject: vi.fn(),
  setProject: vi.fn(),
  updatePreferences: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("../../stores/workbench", () => ({
  useWorkbenchStore: () => mockStore,
}));


describe("scenic focused dialogs", () => {
  beforeEach(() => {
    mockStore.error = "";
    mockStore.createProject.mockReset();
    mockStore.setProject.mockReset();
    mockStore.updatePreferences.mockReset();
    mockStore.logout.mockReset();
  });

  it("owns scenic project selection and creation state", async () => {
    const wrapper = mount(ScenicProjectView, { props: { open: true } });

    expect(wrapper.get('[role="dialog"]').attributes("aria-labelledby")).toBe("scenic-project-dialog-title");
    expect(wrapper.text()).toContain("西湖景区");
    await wrapper.get(".project-row").trigger("click");
    expect(mockStore.setProject).toHaveBeenCalledWith("scenic", 2);
    expect(wrapper.emitted("close")).toHaveLength(1);

    await wrapper.get('input[placeholder="景区名称"]').setValue("新景区");
    await wrapper.get('input[placeholder="项目描述"]').setValue("新项目");
    await wrapper.get('input[placeholder^="默认相机源"]').setValue("main-camera");
    const createButton = wrapper.findAll("button").find((button) => button.text().includes("创建景区项目"));
    await createButton?.trigger("click");
    expect(mockStore.createProject).toHaveBeenCalledWith(
      "scenic",
      "新景区",
      "新项目",
      "scenic",
      "main-camera",
    );
  });

  it("owns scenic account settings and logout behavior", async () => {
    const wrapper = mount(ScenicAccountDialog, { props: { open: true } });

    expect(wrapper.text()).toContain("operator@example.com");
    const checkbox = wrapper.get('input[type="checkbox"]');
    await checkbox.setValue(false);
    expect(mockStore.updatePreferences).toHaveBeenCalledWith({ autoRefresh: false });

    const logoutButton = wrapper.findAll("button").find((button) => button.text().includes("退出景区账号"));
    await logoutButton?.trigger("click");
    expect(mockStore.logout).toHaveBeenCalledWith("scenic");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
