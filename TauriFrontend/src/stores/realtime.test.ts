import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useRealtimeStore } from "./realtime";
const api = vi.hoisted(() => ({ start: vi.fn(), control: vi.fn(), ticket: vi.fn(), streamUrl: vi.fn() }));
vi.mock("../lib/api/realtime", () => ({ realtimeApi: api }));
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  onopen?: () => void;
  onclose?: () => void;
  onmessage?: (event: { data: string }) => Promise<void>;
  send = vi.fn();
  close() { this.readyState = 3; this.onclose?.(); }
  constructor() { Socket.instances.push(this); }
}
let finishDecode: () => void;
class FrameImage {
  src = "";
  decode() { return new Promise<void>(resolve => { finishDecode = resolve; }); }
}
const packet = { type: "frame", session_id: "session", epoch: 0, sequence: 1, sent_at: Date.now()/1000, processing_ms: 50, jpeg: "fake" };
beforeEach(() => {
  setActivePinia(createPinia()); vi.clearAllMocks(); Socket.instances = [];
  vi.stubGlobal("WebSocket", Socket); vi.stubGlobal("Image", FrameImage);
  api.start.mockResolvedValue({ id: "session", status: "running", epoch: 0 });
  api.control.mockResolvedValue({ id: "session", status: "paused", epoch: 1 });
  api.ticket.mockResolvedValue({ ticket: "ticket" }); api.streamUrl.mockReturnValue("ws://localhost/stream");
});
describe("realtime asynchronous isolation", () => {
  it("clears the camera reconnect warning when a fresh frame recovers", async () => {
    const store = useRealtimeStore();
    await store.start(7,"ref",[0,0,1,1],{camera:"0"},false);
    const socket = Socket.instances[0];
    const staleDecode = socket.onmessage!({ data: JSON.stringify(packet) });
    await socket.onmessage!({ data: JSON.stringify({ type: "status", status: "running", epoch: 0,
      error: "相机暂未输出新画面，等待重新连接" }) });
    expect(store.error).toContain("重新连接");
    finishDecode();
    await staleDecode;
    expect(store.error).toContain("重新连接");
    expect(store.image).toBe("");
    const decoding = socket.onmessage!({ data: JSON.stringify({ ...packet, sequence: 2 }) });
    finishDecode();
    await decoding;
    expect(store.error).toBe("");
    expect(store.frame?.sequence).toBe(2);
    await store.stop();
  });
  it("cannot publish a decoded frame after the user leaves", async () => {
    const store = useRealtimeStore();
    await store.start(7,"ref",[0,0,1,1],{camera:"0"},false);
    const message = Socket.instances[0].onmessage!({ data: JSON.stringify(packet) });
    await store.stop(); finishDecode(); await message;
    expect(store.image).toBe(""); expect(store.frame).toBeNull();
    expect(api.control).toHaveBeenCalledWith(7,"session","stop");
  });
  it("rejects a pending decode and late old-epoch packets after pause", async () => {
    const store = useRealtimeStore();
    await store.start(7,"ref",[0,0,1,1],{camera:"0"},false);
    const ws = Socket.instances[0];
    const first = ws.onmessage!({data:JSON.stringify(packet)});
    await store.control("pause"); finishDecode(); await first;
    api.control.mockResolvedValue({status:"running",epoch:2});
    await store.control("resume");
    const stale = ws.onmessage!({data:JSON.stringify(packet)}); finishDecode(); await stale;
    expect(store.image).toBe(""); expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ack:1}));
    await store.stop();
  });
  it("stops a session whose creation finishes after navigation", async () => {
    let resolve!: (value: unknown) => void;
    api.start.mockImplementation(() => new Promise(done => { resolve = done; }));
    const store = useRealtimeStore();
    const starting = store.start(7,"ref",[0,0,1,1],{camera:"0"},false);
    await Promise.resolve(); await store.stop();
    resolve({id:"session",status:"running",epoch:0}); await starting;
    expect(Socket.instances).toHaveLength(0);
    expect(api.control).toHaveBeenCalledWith(7,"session","stop");
  });
});
