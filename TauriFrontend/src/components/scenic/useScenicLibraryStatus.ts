import { computed } from "vue";

import { useWorkbenchStore } from "../../stores/workbench";


export function clampProgress(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function progressPercent(value?: number): string {
  return `${Math.round(clampProgress(Number(value || 0)) * 100)}%`;
}

export function useScenicLibraryStatus() {
  const store = useWorkbenchStore();
  const runningCameraCount = computed(() =>
    store.scenicCameraWall.filter((item) => item.monitor_running || item.running).length,
  );
  const ingestingCameraCount = computed(() => store.scenicCameraWall.filter((item) => item.running).length);
  const activeCameraPointCount = computed(() =>
    store.scenicCameraPoints.filter((item) => item.is_active).length,
  );
  const targetCount = computed(() =>
    Number(store.scenicProjectStatus?.indexed_targets ?? store.scenicMediaItems.reduce((sum, item) => sum + Number(item.target_count || 0), 0)),
  );
  const indexedMediaCount = computed(() =>
    Number(store.scenicProjectStatus?.indexed_media ?? store.scenicMediaItems.filter((item) => item.status === "indexed").length),
  );
  const latestIndexJob = computed(() =>
    store.jobs.find((job) => ["media_index", "camera_stream"].includes(job.type || job.job_type || "") && ["queued", "running"].includes(job.status)) || null,
  );
  const failedJob = computed(() => store.jobs.find((job) => ["media_index", "camera_stream"].includes(job.type || job.job_type || "") && job.status === "failed") || null);
  const libraryProgress = computed(() => {
    const job = latestIndexJob.value;
    if (job && ["queued", "running"].includes(job.status)) return clampProgress(Number(job.progress || 0.05));
    if (targetCount.value > 0) return 1;
    if ((store.scenicProjectStatus?.processing_media || 0) > 0) return 0.05;
    if (failedJob.value || (store.scenicProjectStatus?.failed_media || 0) > 0) return 1;
    return 0;
  });
  const libraryStatusText = computed(() => {
    const job = latestIndexJob.value;
    if (job) {
      if (job.error) return "入库异常";
      if (job.status === "queued") return "等待目标检测";
      return (job.type || job.job_type) === "camera_stream" ? "实时入库中" : "目标检测与特征提取中";
    }
    if (targetCount.value > 0) return "可匹配";
    if (failedJob.value || (store.scenicProjectStatus?.failed_media || 0) > 0) return "入库异常";
    if ((store.scenicProjectStatus?.processing_media || 0) > 0) return "等待处理";
    return "等待入库";
  });
  const libraryDetailText = computed(() => {
    const job = latestIndexJob.value;
    if (job && ["queued", "running"].includes(job.status)) return job.error || job.message || "正在处理录像，请稍候";
    if (targetCount.value > 0) return `已入库 ${targetCount.value} 个目标，媒体 ${indexedMediaCount.value} 个`;
    if (failedJob.value) return failedJob.value.error || failedJob.value.message || "存在处理失败的视频";
    if ((store.scenicProjectStatus?.processing_media || 0) > 0) return "视频已上传，等待目标检测启动";
    return "导入录像或启用实时入库后，即可匹配游客画面";
  });

  return {
    runningCameraCount,
    ingestingCameraCount,
    activeCameraPointCount,
    targetCount,
    indexedMediaCount,
    libraryProgress,
    libraryStatusText,
    libraryDetailText,
  };
}
