import { beforeEach, describe, expect, it, vi } from "vitest";
import { save } from "@tauri-apps/plugin-dialog";
import { writeFile } from "@tauri-apps/plugin-fs";

import {
  apiRequest,
  readToken,
  saveBlob,
  subscribeAuthSession,
  writeTokens,
} from "./http";


vi.mock("@tauri-apps/plugin-dialog", () => ({ save: vi.fn() }));
vi.mock("@tauri-apps/plugin-fs", () => ({ writeFile: vi.fn() }));


function unauthorized(): Response {
  return new Response(JSON.stringify({ detail: "unauthorized" }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}


describe("scoped authentication recovery", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
    delete window.__TAURI_INTERNALS__;
  });

  it("writes blobs through the native save dialog in Tauri", async () => {
    window.__TAURI_INTERNALS__ = {} as typeof window.__TAURI_INTERNALS__;
    vi.mocked(save).mockResolvedValue("C:\\Users\\tester\\Downloads\\report.csv");
    vi.mocked(writeFile).mockResolvedValue(undefined);

    await expect(saveBlob(new Blob(["id,name\n1,test"]), "report.csv")).resolves.toBe(true);

    expect(save).toHaveBeenCalledWith({ defaultPath: "report.csv" });
    expect(writeFile).toHaveBeenCalledWith(
      "C:\\Users\\tester\\Downloads\\report.csv",
      expect.any(Uint8Array),
    );
  });

  it("does not refresh or emit expiry when a protected request has no session", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(unauthorized());
    const changes: unknown[] = [];
    const unsubscribe = subscribeAuthSession((change) => changes.push(change));

    await expect(apiRequest("/api/v1/search/history", { authScope: "scenic" })).rejects.toMatchObject({
      status: 401,
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(changes).toEqual([]);
    unsubscribe();
  });

  it("expires only the scope whose refresh token is rejected", async () => {
    writeTokens("core", "core-access", "core-refresh");
    writeTokens("scenic", "scenic-access", "scenic-refresh");
    vi.mocked(fetch)
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(unauthorized());

    await expect(apiRequest("/api/v1/projects", { authScope: "core" })).rejects.toMatchObject({
      status: 401,
    });

    expect(readToken("core")).toBe("");
    expect(readToken("core", "refresh")).toBe("");
    expect(readToken("scenic")).toBe("scenic-access");
    expect(readToken("scenic", "refresh")).toBe("scenic-refresh");
  });
});


describe("production request feedback", () => {
  beforeEach(() => {
    delete window.__TAURI_INTERNALS__;
    vi.stubGlobal("fetch", vi.fn());
  });

  it("rejects SPA HTML instead of treating it as an API result", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("<html></html>", { headers: { "content-type": "text/html" } }));
    await expect(apiRequest("/api/v1/auth/login")).rejects.toThrow("反向代理");
  });

  it("times out even when the transport never settles", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
      const assertion = expect(apiRequest("/api/v1/auth/login", { timeoutMs: 100 })).rejects.toMatchObject({ status: 408 });
      await vi.advanceTimersByTimeAsync(100);
      await assertion;
      expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
