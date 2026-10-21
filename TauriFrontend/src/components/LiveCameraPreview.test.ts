import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import LiveCameraPreview from "./LiveCameraPreview.vue";
import { apiRequest } from "../lib/api/http";

vi.mock("../lib/api/http", () => ({ apiRequest: vi.fn(), normalizeAssetUrl: (url: string) => url }));

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

async function fixture() {
  const wrapper = mount(LiveCameraPreview, { props: { src: "/frame?v=1", alt: "Live camera" } });
  await wrapper.get('img[src="/frame?v=1"]').trigger("load");
  return wrapper;
}

describe("buffered live camera preview", () => {
  it("keeps the previous frame visible until the next frame finishes decoding", async () => {
    const wrapper = await fixture();
    await wrapper.setProps({ src: "/frame?v=2" });
    const next = wrapper.get('img[src="/frame?v=2"]');
    let decoded!: () => void;
    (next.element as HTMLImageElement).decode = () => new Promise<void>((resolve) => { decoded = resolve; });
    await next.trigger("load");
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=1");
    expect(next.attributes("aria-hidden")).toBe("true");
    decoded();
    await flushPromises();
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=2");
    wrapper.unmount();
  });

  it("retains the last good frame after a download or decode failure", async () => {
    const wrapper = await fixture();
    await wrapper.setProps({ src: "/frame?v=2" });
    await wrapper.get('img[src="/frame?v=2"]').trigger("error");
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=1");
    await wrapper.setProps({ src: "/frame?v=3" });
    const corrupt = wrapper.get('img[src="/frame?v=3"]');
    (corrupt.element as HTMLImageElement).decode = async () => { throw new Error("corrupt JPEG"); };
    await corrupt.trigger("load");
    await flushPromises();
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=1");
    await wrapper.setProps({ src: "/frame?v=4" });
    (corrupt.element as HTMLImageElement).decode = async () => {};
    await wrapper.get('img[src="/frame?v=4"]').trigger("load");
    await flushPromises();
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=4");
    wrapper.unmount();
  });

  it("finishes slow frames and skips intermediate versions without growing a queue", async () => {
    const wrapper = await fixture();
    await wrapper.setProps({ src: "/frame?v=2" });
    await wrapper.setProps({ src: "/frame?v=3" });
    await wrapper.setProps({ src: "/frame?v=4" });
    expect(wrapper.findAll("img")).toHaveLength(2);
    expect(wrapper.find('img[src="/frame?v=3"]').exists()).toBe(false);
    expect(wrapper.find('img[src="/frame?v=4"]').exists()).toBe(false);
    await wrapper.get('img[src="/frame?v=2"]').trigger("load");
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=2");
    await wrapper.get('img[src="/frame?v=4"]').trigger("load");
    expect(wrapper.get('img[alt="Live camera"]').attributes("src")).toBe("/frame?v=4");
    wrapper.unmount();
  });

  it("ignores a pending decode after the camera source is cleared", async () => {
    const wrapper = await fixture();
    await wrapper.setProps({ src: "/frame?v=2" });
    let decoded!: () => void;
    const next = wrapper.get('img[src="/frame?v=2"]');
    (next.element as HTMLImageElement).decode = () => new Promise<void>((resolve) => { decoded = resolve; });
    await next.trigger("load");
    await wrapper.setProps({ src: "" });
    decoded();
    await flushPromises();
    expect(wrapper.find('img[alt="Live camera"]').exists()).toBe(false);
    expect(wrapper.findAll("img[src]")).toHaveLength(0);
    wrapper.unmount();
  });
});

describe("continuous preview stream", () => {
  it("uses scoped authentication once and keeps the stream across snapshot updates", async () => {
    vi.useFakeTimers();
    vi.mocked(apiRequest).mockResolvedValue({ url: "/preview.mjpeg?token=first", expires_in: 300 });
    const wrapper = mount(LiveCameraPreview, { props: {
      src: "/frame?v=1", alt: "Live", streamEndpoint: "/scenic/stream", authScope: "scenic",
    } });
    await flushPromises();
    expect(apiRequest).toHaveBeenCalledWith("/scenic/stream", expect.objectContaining({ authScope: "scenic" }));
    await wrapper.get('img[src="/frame?v=1"]').trigger("load");
    expect(wrapper.get('img[alt="Live"]').attributes("src")).toBe("/frame?v=1");
    await wrapper.get("img.stream").trigger("load");
    await wrapper.setProps({ src: "/frame?v=8" });
    expect(wrapper.find('img[src="/frame?v=8"]').exists()).toBe(false);
    expect(wrapper.get('img[alt="Live"]').attributes("src")).toContain("preview.mjpeg");
    expect(apiRequest).toHaveBeenCalledTimes(1);
    vi.mocked(apiRequest).mockResolvedValue({ url: "/preview.mjpeg?token=renewed", expires_in: 300 });
    await vi.advanceTimersByTimeAsync(295000);
    expect(wrapper.get("img.stream").attributes("src")).toContain("renewed");
    wrapper.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("retries a failed stream and aborts a pending request when disposed", async () => {
    vi.useFakeTimers();
    vi.mocked(apiRequest).mockResolvedValue({ url: "/preview.mjpeg", expires_in: 300 });
    const wrapper = mount(LiveCameraPreview, { props: { src: "/frame?v=1", alt: "Live", streamEndpoint: "/stream" } });
    await flushPromises();
    await wrapper.get("img.stream").trigger("error");
    expect(wrapper.find("img.stream").exists()).toBe(false);
    vi.mocked(apiRequest).mockImplementation(() => new Promise(() => {}));
    await vi.advanceTimersByTimeAsync(2000);
    const calls = vi.mocked(apiRequest).mock.calls;
    const signal = calls[calls.length - 1][1]!.signal!;
    expect(signal.aborted).toBe(false);
    wrapper.unmount();
    expect(signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ignores a stale signed URL after switching camera", async () => {
    let finish!: (value: unknown) => void;
    vi.mocked(apiRequest).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const wrapper = mount(LiveCameraPreview, { props: { src: "/frame?v=1", alt: "Live", streamEndpoint: "/camera/1" } });
    vi.mocked(apiRequest).mockResolvedValue({ url: "/camera2.mjpeg", expires_in: 300 });
    await wrapper.setProps({ streamEndpoint: "/camera/2" });
    await flushPromises();
    finish({ url: "/camera1.mjpeg", expires_in: 300 });
    await flushPromises();
    expect(wrapper.get("img.stream").attributes("src")).toBe("/camera2.mjpeg");
    wrapper.unmount();
  });
});
