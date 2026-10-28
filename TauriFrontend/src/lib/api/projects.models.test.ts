import { afterEach, expect, it, vi } from "vitest";
import { projectsApi } from "./projects";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

it("explains an old backend's missing model route", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not Found" }),
    { status: 404, headers: { "content-type": "application/json" } })));
  await expect(projectsApi.models("core")).rejects.toThrow("当前后端缺少多模型接口");
});

it("rejects a wrong proxy response and uses the scenic model route", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ wrong: [] }),
    { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(projectsApi.models("scenic")).rejects.toThrow("模型接口返回格式错误");
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/scenic/models");
});
