from __future__ import annotations

import contextvars
import json
import logging
import re
import shutil
import time
import traceback
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping


request_id_var = contextvars.ContextVar("request_id", default="")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
UNMATCHED_ROUTE_LABEL = "<unmatched>"


_LOG_RECORD_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_RESERVED or key.startswith("_"):
                continue
            payload[key] = _json_safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        from app.services.redaction import redact_credentials
        return redact_credentials(json.dumps(payload, ensure_ascii=False, default=str))


def configure_logging(level: str = "INFO") -> None:
    # OAuth exchanges put credentials in query strings; never emit client wire logs.
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))


def begin_request(request_id: str | None = None) -> str:
    candidate = (request_id or "").strip()
    resolved = candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else str(uuid.uuid4())
    request_id_var.set(resolved)
    return resolved


def current_request_id() -> str:
    return request_id_var.get("")


def clear_request() -> None:
    request_id_var.set("")


def route_template(request) -> str:
    route = request.scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if path:
            return str(path)
    # Never use a caller-controlled raw URL as a metrics label. A stream of
    # unique 404 paths would otherwise grow every Counter in this module
    # without bound.
    return UNMATCHED_ROUTE_LABEL


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except TypeError:
        return str(value)


@dataclass
class ErrorRecord:
    id: str
    timestamp: str
    request_id: str
    error_type: str
    message: str
    method: str
    path: str
    status_code: int | None
    traceback: str


class ErrorTracker:
    def __init__(self, max_items: int = 100) -> None:
        self._items: deque[ErrorRecord] = deque(maxlen=max_items)
        self._counts: Counter[tuple[str, str]] = Counter()
        self._lock = Lock()

    def record_exception(self, exc: Exception, *, method: str, path: str, status_code: int | None = None) -> str:
        error_id = str(uuid.uuid4())
        record = ErrorRecord(
            id=error_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            request_id=current_request_id(),
            error_type=exc.__class__.__name__,
            message=str(exc),
            method=method,
            path=path,
            status_code=status_code,
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        with self._lock:
            self._items.appendleft(record)
            self._counts[(record.error_type, path)] += 1
        runtime_metrics.observe_error(record.error_type, path)
        return error_id

    def record_http_error(self, *, method: str, path: str, status_code: int) -> str:
        error_id = str(uuid.uuid4())
        record = ErrorRecord(
            id=error_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            request_id=current_request_id(),
            error_type=f"HTTP_{status_code}",
            message=f"HTTP {status_code}",
            method=method,
            path=path,
            status_code=status_code,
            traceback="",
        )
        with self._lock:
            self._items.appendleft(record)
            self._counts[(record.error_type, path)] += 1
        runtime_metrics.observe_error(record.error_type, path)
        return error_id

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) for item in list(self._items)[:limit]]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": sum(self._counts.values()),
                "by_type_path": [
                    {"error_type": error_type, "path": path, "count": count}
                    for (error_type, path), count in self._counts.items()
                ],
                "last_error": asdict(self._items[0]) if self._items else None,
            }


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests_total: Counter[tuple[str, str, str]] = Counter()
        self._request_duration_sum: defaultdict[tuple[str, str, str], float] = defaultdict(float)
        self._request_duration_count: Counter[tuple[str, str, str]] = Counter()
        self._errors_total: Counter[tuple[str, str]] = Counter()
        self._task_duration_sum: defaultdict[tuple[str, str], float] = defaultdict(float)
        self._task_duration_count: Counter[tuple[str, str]] = Counter()
        self._model_load_seconds: dict[str, float] = {}

    def observe_request(self, *, method: str, path: str, status_code: int, duration_seconds: float) -> None:
        status = str(status_code)
        status_class = f"{status_code // 100}xx" if status_code > 0 else "unknown"
        with self._lock:
            self._requests_total[(method, path, status)] += 1
            self._request_duration_sum[(method, path, status_class)] += max(0.0, duration_seconds)
            self._request_duration_count[(method, path, status_class)] += 1

    def observe_error(self, error_type: str, path: str) -> None:
        with self._lock:
            self._errors_total[(error_type, path)] += 1

    def observe_task_duration(self, task_type: str, status: str, duration_seconds: float) -> None:
        with self._lock:
            self._task_duration_sum[(task_type, status)] += max(0.0, duration_seconds)
            self._task_duration_count[(task_type, status)] += 1

    def set_model_load_seconds(self, component: str, duration_seconds: float) -> None:
        with self._lock:
            self._model_load_seconds[component] = max(0.0, duration_seconds)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_total": dict(self._requests_total),
                "request_duration_sum": dict(self._request_duration_sum),
                "request_duration_count": dict(self._request_duration_count),
                "errors_total": dict(self._errors_total),
                "task_duration_sum": dict(self._task_duration_sum),
                "task_duration_count": dict(self._task_duration_count),
                "model_load_seconds": dict(self._model_load_seconds),
            }


def disk_status(
    paths: Mapping[str, Path | str],
    *,
    free_percent_threshold: float,
    free_bytes_threshold: int,
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for label, raw_path in paths.items():
        path = Path(raw_path)
        probe = _existing_probe_path(path)
        usage = shutil.disk_usage(probe)
        free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
        alert = usage.free < free_bytes_threshold or free_percent < free_percent_threshold
        statuses.append(
            {
                "label": label,
                "path": str(path),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "free_percent": round(free_percent, 2),
                "used_percent": round(100.0 - free_percent, 2),
                "alert": alert,
            }
        )
    return statuses


def _existing_probe_path(path: Path) -> str:
    current = path if path.exists() else path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return str(current if current.exists() else Path("."))


def prometheus_escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def prometheus_sample(name: str, value: Any, labels: Mapping[str, Any] | None = None) -> str:
    label_text = ""
    if labels:
        label_text = "{" + ",".join(f'{key}="{prometheus_escape(value)}"' for key, value in labels.items()) + "}"
    return f"{name}{label_text} {float(value)}"


runtime_metrics = RuntimeMetrics()
error_tracker = ErrorTracker()
