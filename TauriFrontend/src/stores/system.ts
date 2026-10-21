import { ref } from "vue";
import { defineStore } from "pinia";

import type { CameraDevice, MetricsPayload, ReadyzPayload } from "../lib/api/system";
import type { WorkbenchMode, WorkbenchPreferences } from "./types";


const DEFAULT_PREFERENCES: WorkbenchPreferences = {
  autoRefresh: true,
  livePollIntervalMs: 1000,
};

function loadPreferences(): WorkbenchPreferences {
  try {
    const stored = JSON.parse(localStorage.getItem("myreid.preferences") || "{}") as Partial<WorkbenchPreferences>;
    return {
      autoRefresh: stored.autoRefresh !== false,
      livePollIntervalMs: Math.max(500, Math.min(5000, Number(stored.livePollIntervalMs) || DEFAULT_PREFERENCES.livePollIntervalMs)),
    };
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}

export const useSystemStore = defineStore("system", () => {
  const mode = ref<WorkbenchMode>("core");
  const activeTaskCount = ref(0);
  const message = ref("系统准备中");
  const error = ref("");
  const readyz = ref<ReadyzPayload | null>(null);
  const metrics = ref<MetricsPayload | null>(null);
  const observability = ref<Record<string, unknown> | null>(null);
  const cameraDevices = ref<CameraDevice[]>([]);
  const scenicCameraDevices = ref<CameraDevice[]>([]);
  const preferences = ref<WorkbenchPreferences>(loadPreferences());

  function updatePreferences(patch: Partial<WorkbenchPreferences>): void {
    preferences.value = {
      autoRefresh: patch.autoRefresh ?? preferences.value.autoRefresh,
      livePollIntervalMs: Math.max(
        500,
        Math.min(5000, Number(patch.livePollIntervalMs ?? preferences.value.livePollIntervalMs)),
      ),
    };
    localStorage.setItem("myreid.preferences", JSON.stringify(preferences.value));
  }

  return {
    mode,
    activeTaskCount,
    message,
    error,
    readyz,
    metrics,
    observability,
    cameraDevices,
    scenicCameraDevices,
    preferences,
    updatePreferences,
  };
});
