import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScenicMusicPicker from "./ScenicMusicPicker.vue";
import type { EditingCatalog } from "../../lib/api/scenicEditing";

const music: EditingCatalog["music"] = [
  { id: "travel", name: "旅途", style: "轻松旅行", source: "OpenGameArt.org", source_url: "https://opengameart.org/content/revelation", author: "Composer", license: "CC0", license_url: "https://creativecommons.org/publicdomain/zero/1.0/", usage: "可商用，无需署名", preview_url: "/api/v1/scenic/editing/music/travel/preview?token=first", duration_seconds: 125, origin: "download" },
  { id: "piano", name: "钢琴", preview_url: "/api/v1/scenic/editing/music/piano/preview?token=second" },
  { id: "legacy", name: "旧配乐" },
];

describe("scenic music picker", () => {
  beforeEach(() => { vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {}); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("shows the selected track's source, commercial use terms and local preview at the configured volume", async () => {
    const wrapper = mount(ScenicMusicPicker, { props: { music, musicId: "travel", volume: .25 } });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("轻松旅行 · 2:05");
    expect(wrapper.text()).toContain("可商用，无需署名");
    expect(wrapper.get('a[href="https://opengameart.org/content/revelation"]').attributes("rel")).toBe("noopener noreferrer");
    const audio = wrapper.get("audio").element;
    expect(audio.getAttribute("src")).toContain("/travel/preview?token=first");
    expect(audio.preload).toBe("none");
    expect(audio.autoplay).toBe(false);
    expect(audio.volume).toBe(.25);
    await wrapper.setProps({ volume: .6 });
    expect(audio.volume).toBe(.6);
    wrapper.unmount();
  });

  it("emits the chosen track unchanged and stops the previous preview when changing tracks or disabling music", async () => {
    const wrapper = mount(ScenicMusicPicker, { props: { music, musicId: "travel", volume: .25 } });
    const previous = wrapper.get("audio").element;
    await wrapper.get("select").setValue("piano");
    expect(wrapper.emitted("update:musicId")).toEqual([["piano"]]);
    await wrapper.setProps({ musicId: "piano" });
    expect(previous.pause).toHaveBeenCalled();
    expect(wrapper.get("audio").attributes("src")).toContain("/piano/preview?token=second");
    expect(wrapper.text()).not.toContain("CC0");
    await wrapper.get("select").setValue("none");
    const changes = wrapper.emitted("update:musicId")!;
    expect(changes[changes.length - 1]).toEqual(["none"]);
    await wrapper.setProps({ musicId: "none" });
    expect(wrapper.find("audio").exists()).toBe(false);
    expect(wrapper.find('[aria-label="配乐信息"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("supports older catalog entries and clears a preview failure on the next selection", async () => {
    const wrapper = mount(ScenicMusicPicker, { props: { music, musicId: "legacy", volume: .25 } });
    expect(wrapper.find("audio").exists()).toBe(false);
    expect(wrapper.get("select").element.value).toBe("legacy");
    await wrapper.setProps({ musicId: "travel" });
    await wrapper.get("audio").trigger("error");
    expect(wrapper.get('[role="status"]').text()).toContain("试听暂不可用");
    await wrapper.setProps({ musicId: "piano" });
    expect(wrapper.find('[role="status"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows music as disabled when an auto draft returns an empty music id", () => {
    const wrapper = mount(ScenicMusicPicker, { props: { music, musicId: "", volume: .25 } });
    expect(wrapper.get("select").element.value).toBe("none");
    expect(wrapper.find("audio").exists()).toBe(false);
    expect(wrapper.find('[aria-label="配乐信息"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
