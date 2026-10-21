import { defineStore } from "pinia";
import { ref } from "vue";
import { realtimeApi, type Crop, type RealtimeFrame, type RealtimeSource } from "../lib/api/realtime";

export const useRealtimeStore = defineStore("realtime", () => {
  const sessionId = ref("");
  const status = ref("idle");
  const error = ref("");
  const image = ref("");
  const frame = ref<RealtimeFrame | null>(null);
  const latency = ref(0);
  let socket: WebSocket | undefined;
  let generation = 0;
  let displayEpoch = 0;
  let controlling = false;
  let remoteEpoch = 0;
  let project = 0;
  let reconnect: ReturnType<typeof setTimeout> | undefined;
  let reconnectUntil = 0;
  const terminal = (value: string) => ["completed", "stopped", "failed"].includes(value);

  async function connect(current: number): Promise<void> {
    try {
      const credential = await realtimeApi.ticket(project, sessionId.value);
      if (current !== generation) return;
      const ws = new WebSocket(realtimeApi.streamUrl(project, sessionId.value, credential.ticket));
      socket = ws;
      ws.onopen = () => { reconnectUntil = Date.now() + 25_000; };
      ws.onmessage = async (event) => {
        if (current !== generation || socket !== ws) return;
        try {
          const message = JSON.parse(event.data);
          if (message.type === "frame") {
            if (message.session_id !== sessionId.value) return;
            const epoch = displayEpoch;
            const preview = new Image();
            preview.src = `data:image/jpeg;base64,${message.jpeg}`;
            await preview.decode();
            if (current !== generation || socket !== ws) return;
            if ((message.epoch ?? 0) === remoteEpoch && epoch === displayEpoch && !controlling && status.value !== "paused") {
              image.value = preview.src;
              frame.value = message;
              // A recovered stream can send frames continuously without a
              // separate status packet. Clear the earlier camera outage here.
              error.value = "";
              latency.value = Math.max(0, Date.now() - message.sent_at * 1000 + message.processing_ms);
            }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ ack: message.sequence }));
          } else {
            if (!controlling && (message.epoch ?? 0) >= remoteEpoch) {
              remoteEpoch = message.epoch ?? 0; status.value = message.status;
            }
            error.value = message.error || "";
            if (message.error || terminal(message.status)) {
              // Do not let an earlier asynchronous decode restore stale video
              // or clear an outage reported after that frame was received.
              ++displayEpoch; image.value = ""; frame.value = null;
            }
            if (!terminal(message.status) && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ pong: true }));
          }
        } catch { error.value = "画面解码失败，正在重新连接"; ws.close(); }
      };
      ws.onclose = () => {
        if (current !== generation || terminal(status.value)) return;
        if (Date.now() >= reconnectUntil) { error.value = "流连接中断，请重新开始"; void stop(); return; }
        status.value = "reconnecting"; image.value = ""; frame.value = null;
        reconnect = setTimeout(() => void connect(current), 1000);
      };
    } catch (reason) {
      if (current !== generation) return;
      error.value = String(reason);
      if (Date.now() < reconnectUntil) reconnect = setTimeout(() => void connect(current), 1000);
      else void stop();
    }
  }

  async function start(projectId: number, reference: string, crop: Crop, source: RealtimeSource, others: boolean) {
    const stopping = stop();
    const expected = generation;
    await stopping;
    if (expected !== generation) return;
    const current = ++generation;
    project = projectId; status.value = "starting"; error.value = "";
    try {
      const session = await realtimeApi.start(projectId, reference, crop, source, others);
      if (current !== generation) { await realtimeApi.control(projectId, session.id, "stop"); return; }
      sessionId.value = session.id; status.value = session.status; remoteEpoch = session.epoch ?? 0;
      reconnectUntil = Date.now() + 25_000;
      await connect(current);
    } catch (reason) {
      if (current === generation) { error.value = String(reason); status.value = "failed"; }
    }
  }

  async function control(action: "pause" | "resume") {
    const current = generation;
    ++displayEpoch; controlling = true;
    try {
      // Invalidate queued decodes before issuing control.
      image.value = ""; frame.value = null;
      const response = await realtimeApi.control(project, sessionId.value, action);
      if (current === generation) { status.value = response.status; remoteEpoch = response.epoch ?? 0; }
    } catch (reason) { if (current === generation) error.value = String(reason); }
    finally { if (current === generation) controlling = false; }
  }

  async function stop() {
    ++generation; ++displayEpoch; controlling = false;
    clearTimeout(reconnect);
    const id = sessionId.value, previousProject = project;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ action: "stop" }));
    socket?.close(); socket = undefined;
    sessionId.value = ""; image.value = ""; frame.value = null; status.value = "idle";
    if (id) {
      try { await realtimeApi.control(previousProject, id, "stop"); }
      catch { /* Socket disconnect also triggers the backend's bounded 30-second lease. */ }
    }
  }
  return { sessionId, status, error, image, frame, latency, start, control, stop };
});
