import datetime
from typing import List

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, SummaryMetricFamily
from sqlalchemy.orm import Session

import database
from app.core.config import get_settings
from app.core.observability import disk_status, error_tracker, runtime_metrics
from app.services.job_manager import job_manager
from app.services.model_runtime import model_status
from app.services.vector_store import vector_store


BASE_GALLERY_DIR = str(get_settings().gallery_dir)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _disk_status_payload() -> List[dict]:
    settings = get_settings()
    return disk_status(
        {
            "gallery": BASE_GALLERY_DIR,
            "object_storage": settings.object_storage_dir,
        },
        free_percent_threshold=settings.disk_alert_free_percent,
        free_bytes_threshold=settings.disk_alert_free_bytes,
    )


def _job_observability_payload(db: Session, project_ids: List[int] = None) -> dict:
    query = db.query(database.ProcessingJob)
    if project_ids is not None:
        if not project_ids:
            return {"total": 0, "by_status": [], "durations": [], "active": []}
        query = query.filter(database.ProcessingJob.project_id.in_(project_ids))

    now = _utc_now()
    status_counts = {}
    durations = {}
    active = []
    rows = query.all()
    for job in rows:
        count_key = (job.job_type, job.status)
        status_counts[count_key] = status_counts.get(count_key, 0) + 1
        job_created_at = _as_utc(job.created_at) if job.created_at else None
        job_updated_at = _as_utc(job.updated_at) if job.updated_at else None
        if job_created_at and job_updated_at:
            seconds = max(0.0, (job_updated_at - job_created_at).total_seconds())
            duration = durations.setdefault(count_key, {"count": 0, "sum_seconds": 0.0, "avg_seconds": 0.0})
            duration["count"] += 1
            duration["sum_seconds"] += seconds
        if job.status in {"queued", "running"} and job_created_at:
            active.append(
                {
                    "id": job.id,
                    "type": job.job_type,
                    "status": job.status,
                    "project_id": job.project_id,
                    "age_seconds": round(max(0.0, (now - job_created_at).total_seconds()), 3),
                }
            )

    duration_items = []
    for (job_type, status_name), data in durations.items():
        if data["count"]:
            data["avg_seconds"] = round(data["sum_seconds"] / data["count"], 3)
        duration_items.append(
            {
                "type": job_type,
                "status": status_name,
                "count": data["count"],
                "sum_seconds": round(data["sum_seconds"], 3),
                "avg_seconds": data["avg_seconds"],
            }
        )

    return {
        "total": len(rows),
        "by_status": [
            {"type": job_type, "status": status_name, "count": count}
            for (job_type, status_name), count in status_counts.items()
        ],
        "durations": duration_items,
        "active": active[:20],
    }


def _runtime_metrics_payload() -> dict:
    snapshot = runtime_metrics.snapshot()
    return {
        "requests_total": [
            {"method": method, "path": path, "status": status_code, "count": count}
            for (method, path, status_code), count in snapshot["requests_total"].items()
        ],
        "request_duration": [
            {
                "method": method,
                "path": path,
                "status_class": status_class,
                "sum_seconds": round(total, 6),
                "count": snapshot["request_duration_count"].get((method, path, status_class), 0),
            }
            for (method, path, status_class), total in snapshot["request_duration_sum"].items()
        ],
        "errors_total": [
            {"type": error_type, "path": path, "count": count}
            for (error_type, path), count in snapshot["errors_total"].items()
        ],
        "task_duration": [
            {
                "type": task_type,
                "status": status_name,
                "sum_seconds": round(total, 6),
                "count": snapshot["task_duration_count"].get((task_type, status_name), 0),
            }
            for (task_type, status_name), total in snapshot["task_duration_sum"].items()
        ],
        "model_load_seconds": snapshot["model_load_seconds"],
    }


def _service_observability_payload(db: Session = None, project_ids: List[int] = None) -> dict:
    db_ready = True
    db_error = None
    try:
        database.check_database_connection()
    except Exception as exc:
        db_ready = False
        db_error = str(exc)

    try:
        queue_stats = job_manager.stats()
    except Exception as exc:
        queue_stats = {
            "backend": "redis-rq",
            "redis_ready": False,
            "ready": False,
            "queue_mode": "unavailable",
            "error": str(exc),
            "queued": 0,
        }

    try:
        vector_status = vector_store.status()
    except Exception as exc:
        vector_status = {"ready": False, "error": str(exc)}

    models = model_status()
    disks = _disk_status_payload()
    disk_alert = any(item["alert"] for item in disks)
    queue_ready = bool(queue_stats.get("ready"))
    dependencies_ready = db_ready and queue_ready and bool(vector_status.get("ready"))
    degraded_reasons = []
    if queue_stats.get("queue_mode") == "local":
        degraded_reasons.append("queue_local_fallback")
    if disk_alert:
        degraded_reasons.append("disk_capacity")
    if not dependencies_ready:
        status_text = "unhealthy"
    elif not models.get("ready"):
        status_text = "warming"
    elif degraded_reasons:
        status_text = "degraded"
    else:
        status_text = "ready"

    return {
        "status": status_text,
        "degraded_reasons": degraded_reasons,
        "models": models,
        "database": {**database.get_database_info(), "ready": db_ready, "error": db_error},
        "queue": queue_stats,
        "vector_store": vector_status,
        "disk": disks,
        "errors": error_tracker.summary(),
        "jobs": _job_observability_payload(db, project_ids) if db is not None else None,
        "metrics": _runtime_metrics_payload(),
    }


def _prometheus_payload(db: Session) -> str:
    snapshot = runtime_metrics.snapshot()
    models = model_status()
    queue_stats = job_manager.stats()
    disks = _disk_status_payload()
    vector_status = vector_store.status()
    job_stats = _job_observability_payload(db)
    db_ready = 1
    try:
        database.check_database_connection()
    except Exception:
        db_ready = 0

    registry = CollectorRegistry(auto_describe=False)

    requests = CounterMetricFamily(
        "myreid_http_requests_total",
        "Total HTTP requests by method, route, and status.",
        labels=["method", "path", "status"],
    )
    for (method, path, status_code), value in snapshot["requests_total"].items():
        requests.add_metric([str(method), str(path), str(status_code)], value)
    registry.register(_MetricFamilyCollector(requests))

    request_duration = SummaryMetricFamily(
        "myreid_http_request_duration_seconds",
        "HTTP request duration summary.",
        labels=["method", "path", "status_class"],
    )
    for (method, path, status_class), value in snapshot["request_duration_sum"].items():
        count = snapshot["request_duration_count"].get((method, path, status_class), 0)
        request_duration.add_metric([str(method), str(path), str(status_class)], count, value)
    registry.register(_MetricFamilyCollector(request_duration))

    errors = CounterMetricFamily(
        "myreid_errors_total",
        "Total tracked server errors.",
        labels=["type", "path"],
    )
    for (error_type, path), value in snapshot["errors_total"].items():
        errors.add_metric([str(error_type), str(path)], value)
    registry.register(_MetricFamilyCollector(errors))

    processing_jobs = GaugeMetricFamily(
        "myreid_processing_jobs",
        "Processing jobs by type and status.",
        labels=["type", "status"],
    )
    for item in job_stats["by_status"]:
        processing_jobs.add_metric([str(item["type"]), str(item["status"])], item["count"])
    registry.register(_MetricFamilyCollector(processing_jobs))

    processing_duration = SummaryMetricFamily(
        "myreid_processing_job_duration_seconds",
        "Processing job duration summary from persisted jobs.",
        labels=["type", "status"],
    )
    for item in job_stats["durations"]:
        processing_duration.add_metric(
            [str(item["type"]), str(item["status"])], item["count"], item["sum_seconds"]
        )
    registry.register(_MetricFamilyCollector(processing_duration))

    runtime_task_duration = SummaryMetricFamily(
        "myreid_runtime_task_duration_seconds",
        "Runtime task duration summary.",
        labels=["type", "status"],
    )
    for (task_type, status_name), value in snapshot["task_duration_sum"].items():
        count = snapshot["task_duration_count"].get((task_type, status_name), 0)
        runtime_task_duration.add_metric([str(task_type), str(status_name)], count, value)
    registry.register(_MetricFamilyCollector(runtime_task_duration))

    model_ready = GaugeMetricFamily(
        "myreid_model_ready", "Model service ready state.", labels=["backend"]
    )
    model_ready.add_metric([str(models.get("backend", "unknown"))], 1 if models.get("ready") else 0)
    registry.register(_MetricFamilyCollector(model_ready))
    model_loaded = GaugeMetricFamily(
        "myreid_model_loaded", "Model component loaded state.", labels=["component", "backend"]
    )
    for component in ("embedding", "detection"):
        component_status = models.get(component, {})
        model_loaded.add_metric(
            [component, str(component_status.get("backend", "unknown"))],
            1 if component_status.get("loaded") else 0,
        )
    registry.register(_MetricFamilyCollector(model_loaded))
    model_load = GaugeMetricFamily(
        "myreid_model_load_seconds", "Model load duration.", labels=["component"]
    )
    for component, seconds in snapshot["model_load_seconds"].items():
        model_load.add_metric([str(component)], seconds)
    registry.register(_MetricFamilyCollector(model_load))

    queue_name = str(queue_stats.get("name", ""))
    for metric_name, help_text, value in (
        ("myreid_queue_length", "Queue length by queue name.", queue_stats.get("queued", 0)),
        ("myreid_queue_ready", "Operational queue ready state.", 1 if queue_stats.get("ready") else 0),
        ("myreid_queue_local_running", "Locally running fallback jobs.", queue_stats.get("local_running", 0)),
    ):
        family = GaugeMetricFamily(metric_name, help_text, labels=["name"])
        family.add_metric([queue_name], value)
        registry.register(_MetricFamilyCollector(family))

    disk_free = GaugeMetricFamily("myreid_disk_free_bytes", "Free bytes by runtime storage path.", labels=["path"])
    disk_used = GaugeMetricFamily("myreid_disk_used_percent", "Used disk percentage.", labels=["path"])
    disk_alert_metric = GaugeMetricFamily("myreid_disk_alert", "Disk capacity alert state.", labels=["path"])
    for item in disks:
        label = [str(item["label"])]
        disk_free.add_metric(label, item["free_bytes"])
        disk_used.add_metric(label, item["used_percent"])
        disk_alert_metric.add_metric(label, 1 if item["alert"] else 0)
    registry.register(_MetricFamilyCollector(disk_free, disk_used, disk_alert_metric))

    dependency_ready = GaugeMetricFamily(
        "myreid_dependency_ready", "Dependency ready state.", labels=["dependency"]
    )
    dependency_ready.add_metric(["database"], db_ready)
    dependency_ready.add_metric(["vector_store"], 1 if vector_status.get("ready") else 0)
    registry.register(_MetricFamilyCollector(dependency_ready))
    return generate_latest(registry).decode("utf-8")


class _MetricFamilyCollector:
    def __init__(self, *families) -> None:
        self.families = families

    def collect(self):
        yield from self.families
