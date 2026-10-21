import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ScenicPaymentPanel from "./ScenicPaymentPanel.vue";

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  store: {
    scenicProjectId: 1, scenicUser: { username: "owner", role: "operator" },
    selectedTourist: { id: 1, vlog_jobs: [], orders: [{ id: 7, order_no: "MANUAL-007", amount_cents: 1990,
      payment_provider: "visitor_manual", provider_trade_state: "WAITING_STAFF", status: "pending" }] },
    markScenicOrderPaid: vi.fn(),
  },
}));
vi.mock("../stores/workbench", () => ({ useWorkbenchStore: () => mocks.store }));
vi.mock("../lib/api/http", () => ({ apiRequest: mocks.request, normalizeAssetUrl: (url: string) => url, downloadAsset: vi.fn() }));

describe("visitor payment in the short film order panel", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  it("lets the project owner review and confirm without global admin role", async () => {
    mocks.request.mockResolvedValue({ can_confirm_payment: true });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const wrapper = mount(ScenicPaymentPanel);
    await flushPromises();
    const button = wrapper.findAll("button").find(item => item.text() === "确认游客付款")!;
    await button.trigger("click");
    expect(mocks.store.markScenicOrderPaid).not.toHaveBeenCalled();
    await button.trigger("click");
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("MANUAL-007 · ¥19.90"));
    expect(mocks.store.markScenicOrderPaid).toHaveBeenCalledOnce();
    expect(mocks.store.markScenicOrderPaid).toHaveBeenCalledWith(7);
    wrapper.unmount(); confirm.mockRestore();
  });
  it("does not expose confirmation to a staff operator without manage permission", async () => {
    mocks.request.mockResolvedValue({ can_confirm_payment: false });
    const wrapper = mount(ScenicPaymentPanel);
    await flushPromises();
    expect(wrapper.findAll("button").some(item => item.text() === "确认游客付款")).toBe(false);
    expect(mocks.store.markScenicOrderPaid).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
