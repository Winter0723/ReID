import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it } from "vitest";

import MediaPreviewDialog, { type MediaPreviewState } from "./MediaPreviewDialog.vue";


const imagePreview: MediaPreviewState = {
  open: true,
  title: "检索原图",
  type: "image",
  source: "blob:image",
  frameSource: "",
  downloadSource: "blob:image",
  fallbackSource: "",
  downloadName: "result.jpg",
  seekSeconds: 0,
  fallbackSeekSeconds: 0,
};


describe("MediaPreviewDialog", () => {
  it("seeks once and does not rewind when canplay fires after manual seeking", async () => {
    const wrapper = mount(MediaPreviewDialog, { attachTo: document.body, props: {
      preview: { ...imagePreview, type: "video", source: "preview.mp4", seekSeconds: 21 },
    } });
    await nextTick();
    const video = document.body.querySelector("video")!;
    Object.defineProperty(video, "readyState", { value: 1 });
    Object.defineProperty(video, "duration", { value: 60 });
    video.dispatchEvent(new Event("loadedmetadata"));
    expect(video.currentTime).toBe(21);
    video.dispatchEvent(new Event("seeked"));
    video.currentTime = 35;
    video.dispatchEvent(new Event("canplay"));
    expect(video.currentTime).toBe(35);
    wrapper.unmount();
  });
  it("renders an accessible image dialog with image download semantics", async () => {
    const wrapper = mount(MediaPreviewDialog, {
      attachTo: document.body,
      props: { preview: imagePreview },
    });

    const dialog = document.body.querySelector('[role="dialog"]');
    expect(dialog?.getAttribute("aria-label")).toBe("检索原图");
    expect(document.body.querySelector("video")).toBeNull();
    expect(
      Array.from(document.body.querySelectorAll("button")).some((button) => button.textContent?.includes("下载图片")),
    ).toBe(true);
    const close = document.body.querySelector('button[aria-label="关闭预览"]');
    expect(close).not.toBeNull();
    close?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("renders video controls with video-specific download semantics", () => {
    const wrapper = mount(MediaPreviewDialog, {
      attachTo: document.body,
      props: {
        preview: {
          ...imagePreview,
          title: "完整视频",
          type: "video",
          source: "video.mp4",
          downloadSource: "video.mp4",
          downloadName: "video.mp4",
        },
      },
    });

    expect(document.body.querySelector("video")).not.toBeNull();
    expect(document.body.textContent).toContain("下载视频");
    expect(document.body.textContent).not.toContain("下载图片");
    wrapper.unmount();
  });

  it("keeps keyboard focus inside the dialog and restores it after close", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "打开预览";
    document.body.appendChild(trigger);
    trigger.focus();

    const wrapper = mount(MediaPreviewDialog, {
      attachTo: document.body,
      props: { preview: { ...imagePreview, open: false } },
    });
    await wrapper.setProps({ preview: { ...imagePreview, open: true } });
    await nextTick();
    await nextTick();
    expect(document.body.querySelector('[role="dialog"]')?.contains(document.activeElement)).toBe(true);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(wrapper.emitted("close")).toHaveLength(1);
    await wrapper.setProps({ preview: { ...imagePreview, open: false } });
    await nextTick();
    await nextTick();
    expect(document.activeElement).toBe(trigger);

    wrapper.unmount();
    trigger.remove();
  });
});
