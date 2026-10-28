import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import ModelSelect from "./ModelSelect.vue";
import { projectsApi } from "../lib/api/projects";

vi.mock("../lib/api/projects", () => ({ projectsApi: { models: vi.fn() } }));
const mgd = (id: string) => ({ id, name: `MGD-DPM ${id}`, available: true, dimension: 1280, capabilities: { image: true, text: false } });
beforeEach(() => vi.mocked(projectsApi.models).mockReset());

it("lists multiple MGD checkpoints and selects an available model when the default is missing", async () => {
  vi.mocked(projectsApi.models).mockResolvedValue([mgd("one"), mgd("two"), mgd("three")]);
  const wrapper = mount(ModelSelect, { props: { scope: "core", open: true, modelValue: "" } });
  await flushPromises();
  expect(wrapper.findAll("option")).toHaveLength(4);
  expect(wrapper.emitted("update:modelValue")?.slice(-1)[0]).toEqual(["one"]);
  await wrapper.setProps({ modelValue: "two" });
  await wrapper.get("button").trigger("click");
  await flushPromises();
  expect(wrapper.emitted("update:modelValue")?.slice(-1)[0]).toEqual(["two"]);
  expect(wrapper.text()).toContain("不支持文本检索");
  wrapper.unmount();
});

it("does not let an old domain response overwrite the current domain", async () => {
  let finishCore: (rows: ReturnType<typeof mgd>[]) => void = () => {};
  vi.mocked(projectsApi.models).mockImplementation(scope => scope === "core"
    ? new Promise(resolve => { finishCore = resolve; }) : Promise.resolve([mgd("scenic")]));
  const wrapper = mount(ModelSelect, { props: { scope: "core", open: true, modelValue: "" } });
  await wrapper.setProps({ scope: "scenic" });
  await flushPromises();
  finishCore([mgd("outdated-core")]);
  await flushPromises();
  expect(wrapper.text()).toContain("MGD-DPM scenic");
  expect(wrapper.text()).not.toContain("outdated-core");
  wrapper.unmount();
});

it("shows the backend version error and allows refresh after recovery", async () => {
  vi.mocked(projectsApi.models).mockRejectedValueOnce(new Error("当前后端缺少多模型接口"));
  const wrapper = mount(ModelSelect, { props: { scope: "core", open: true, modelValue: "" } });
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("缺少多模型接口");
  vi.mocked(projectsApi.models).mockResolvedValue([mgd("recovered")]);
  await wrapper.get("button").trigger("click");
  await flushPromises();
  expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  expect(wrapper.emitted("update:modelValue")?.slice(-1)[0]).toEqual(["recovered"]);
  wrapper.unmount();
});
