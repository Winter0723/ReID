import importlib
import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Optional
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

from app.core.config import get_settings
from app.core.observability import runtime_metrics


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LocalJobFallbackDisabled(RuntimeError):
    """Raised when Redis is unavailable and local job execution is disabled."""


class KeyedTaskPool:
    """Bounded threads for durable DB outboxes; never accumulate an in-memory queue."""

    def __init__(self, workers: int, name: str):
        self._workers = workers
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._lock = threading.Lock()
        self._pending = {}

    def submit(self, key, function, *args):
        with self._lock:
            self._pending = {key: future for key, future in self._pending.items() if not future.done()}
            if key in self._pending or len(self._pending) >= self._workers:
                return False
            self._pending[key] = self._executor.submit(self._run, function, args)
            return True

    @staticmethod
    def _run(function, args):
        try:
            function(*args)
        except Exception:
            logging.getLogger(__name__).exception("Background outbox task failed; durable state will retry")

    def shutdown(self, wait=False):
        self._executor.shutdown(wait=wait, cancel_futures=True)


@dataclass
class JobRecord:
    id: str
    type: str
    status: str
    progress: float
    message: str
    error: Optional[str]
    created_at: datetime
    updated_at: datetime


class RedisJobManager:
    """Redis/RQ-backed job state and dispatch facade."""

    def __init__(self) -> None:
        settings = get_settings()
        self.allow_local_job_fallback = settings.allow_local_job_fallback
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        # RQ stores pickled binary job payloads; metadata uses a separate text client.
        self.queue = Queue(settings.rq_queue_name, connection=Redis.from_url(
            settings.redis_url, decode_responses=False,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        ), default_timeout=settings.rq_job_timeout_seconds)
        self._local_records: dict[str, JobRecord] = {}
        self._logger = logging.getLogger(__name__)
        self._redis_retry_interval_seconds = 30.0
        self._redis_retry_after = 0.0
        self._redis_unavailable = False
        self._local_queue_warning_logged = False
        self._local_lock = threading.Lock()
        self._local_record_limit = max(100, int(os.getenv("LOCAL_JOB_RECORD_LIMIT", "10000")))
        self._redis_record_ttl_seconds = max(
            3600,
            int(os.getenv("JOB_RECORD_TTL_SECONDS", str(7 * 24 * 60 * 60))),
        )
        local_worker_count = max(1, int(os.getenv("LOCAL_JOB_WORKERS", "4")))
        local_queue_limit = max(1, int(os.getenv("LOCAL_JOB_QUEUE_LIMIT", "128")))
        self._local_capacity = threading.BoundedSemaphore(local_worker_count + local_queue_limit)
        self._local_executor = ThreadPoolExecutor(
            max_workers=local_worker_count,
            thread_name_prefix="myreid-local-job",
        )
        self._local_enqueued_total = 0
        self._local_running = 0
        self._local_completed_total = 0
        self._local_failed_total = 0

    def create(self, job_type: str, message: str = "queued", *, job_id: str | None = None) -> JobRecord:
        now = _utc_now()
        record = JobRecord(
            id=job_id or str(uuid4()),
            type=job_type,
            status="queued",
            progress=0.0,
            message=message,
            error=None,
            created_at=now,
            updated_at=now,
        )
        self._write(record)
        return record

    def enqueue(self, job_id: str, task_path: str, *args) -> None:
        if self._can_try_redis():
            try:
                self.queue.enqueue_call(func=task_path, args=args, job_id=job_id)
                self._mark_redis_available()
                return
            except RedisError as exc:
                self._mark_redis_unavailable(exc, "enqueue job")
        if not self.allow_local_job_fallback:
            message = "Redis queue unavailable and ALLOW_LOCAL_JOB_FALLBACK is disabled"
            self._mark_local_fallback_disabled(job_id, message)
            raise LocalJobFallbackDisabled(message)
        if not self._local_queue_warning_logged:
            self._logger.warning("Redis queue unavailable; running media jobs locally until Redis recovers")
            self._local_queue_warning_logged = True
        self._enqueue_local(job_id, task_path, *args)

    def update(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[JobRecord]:
        record = self.get(job_id)
        if not record:
            return None
        previous_status = record.status
        if status is not None:
            record.status = status
        if progress is not None:
            record.progress = max(0.0, min(1.0, progress))
        if message is not None:
            record.message = message
        if error is not None:
            record.error = error
        record.updated_at = _utc_now()
        if record.status in {"succeeded", "failed"} and previous_status not in {"succeeded", "failed"}:
            runtime_metrics.observe_task_duration(
                record.type,
                record.status,
                (record.updated_at - record.created_at).total_seconds(),
            )
        self._write(record)
        return record

    def get(self, job_id: str) -> Optional[JobRecord]:
        if not self._can_try_redis():
            return self._get_local_record(job_id)
        try:
            data = self.redis.hgetall(self._key(job_id))
            self._mark_redis_available()
        except RedisError as exc:
            self._mark_redis_unavailable(exc, "read job state")
            return self._get_local_record(job_id)
        if not data:
            return self._get_local_record(job_id)
        try:
            return JobRecord(
                id=data["id"],
                type=data["type"],
                status=data["status"],
                progress=float(data["progress"]),
                message=data.get("message", ""),
                error=data.get("error") or None,
                created_at=datetime.fromisoformat(data["created_at"]),
                updated_at=datetime.fromisoformat(data["updated_at"]),
            )
        except (KeyError, TypeError, ValueError):
            self._logger.warning("Ignoring malformed Redis job record: %s", job_id)
            return self._get_local_record(job_id)

    def ping(self) -> bool:
        if not self._can_try_redis():
            return False
        try:
            ok = bool(self.redis.ping())
            if ok:
                self._mark_redis_available()
            return ok
        except RedisError as exc:
            self._mark_redis_unavailable(exc, "ping Redis")
            return False

    def stats(self) -> dict:
        with self._local_lock:
            status_counts = Counter(record.status for record in self._local_records.values())
            local_record_count = len(self._local_records)
            local_enqueued_total = self._local_enqueued_total
            local_running = self._local_running
            local_completed_total = self._local_completed_total
            local_failed_total = self._local_failed_total
        stats = {
            "backend": "redis-rq",
            "name": get_settings().rq_queue_name,
            "redis_ready": False,
            "ready": self.allow_local_job_fallback,
            "queue_mode": "local" if self.allow_local_job_fallback else "unavailable",
            "queued": 0,
            "deferred": 0,
            "scheduled": 0,
            "started": 0,
            "failed": 0,
            "local_records": local_record_count,
            "local_status_counts": dict(status_counts),
            "local_enqueued_total": local_enqueued_total,
            "local_running": local_running,
            "local_completed_total": local_completed_total,
            "local_failed_total": local_failed_total,
            "fallback_active": self._redis_unavailable or not self._can_try_redis(),
            "local_fallback_allowed": self.allow_local_job_fallback,
        }
        if not self._can_try_redis():
            return stats
        try:
            redis_ready = bool(self.redis.ping())
            stats.update(
                {
                    "redis_ready": redis_ready,
                    "ready": redis_ready,
                    "queue_mode": "redis" if redis_ready else (
                        "local" if self.allow_local_job_fallback else "unavailable"
                    ),
                    "queued": self.queue.count,
                    "deferred": self._registry_count("deferred_job_registry"),
                    "scheduled": self._registry_count("scheduled_job_registry"),
                    "started": self._registry_count("started_job_registry"),
                    "failed": self._registry_count("failed_job_registry"),
                    "fallback_active": False,
                }
            )
            self._mark_redis_available()
        except (RedisError, AttributeError) as exc:
            self._mark_redis_unavailable(exc, "read queue stats")
            stats["fallback_active"] = True
            stats["ready"] = self.allow_local_job_fallback
            stats["queue_mode"] = "local" if self.allow_local_job_fallback else "unavailable"
        return stats

    def _write(self, record: JobRecord) -> None:
        with self._local_lock:
            self._local_records[record.id] = replace(record)
            self._prune_local_records_unlocked()
        if not self._can_try_redis():
            return
        try:
            key = self._key(record.id)
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.hset(
                key,
                mapping={
                    "id": record.id,
                    "type": record.type,
                    "status": record.status,
                    "progress": str(record.progress),
                    "message": record.message,
                    "error": record.error or "",
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )
            pipeline.expire(key, self._redis_record_ttl_seconds)
            pipeline.execute()
            self._mark_redis_available()
        except RedisError as exc:
            self._mark_redis_unavailable(exc, "persist job state")

    def _enqueue_local(self, job_id: str, task_path: str, *args) -> None:
        if not self._local_capacity.acquire(blocking=False):
            message = "Local fallback queue is full; retry after queued jobs complete"
            self._mark_local_fallback_disabled(job_id, message)
            raise LocalJobFallbackDisabled(message)
        try:
            future = self._local_executor.submit(self._run_local_task, job_id, task_path, args)
            future.add_done_callback(
                lambda completed, queued_job_id=job_id: self._local_task_done(queued_job_id, completed)
            )
        except Exception:
            self._local_capacity.release()
            raise
        with self._local_lock:
            self._local_enqueued_total += 1

    def _run_local_task(self, job_id: str, task_path: str, args: tuple) -> None:
        module_name, func_name = task_path.rsplit(".", 1)
        with self._local_lock:
            self._local_running += 1
        try:
            module = importlib.import_module(module_name)
            getattr(module, func_name)(*args)
            with self._local_lock:
                self._local_completed_total += 1
        except Exception as exc:
            with self._local_lock:
                self._local_failed_total += 1
            self._logger.exception("Local job failed: %s", task_path)
            record = self.get(job_id)
            if record and record.status not in {"succeeded", "failed"}:
                self.update(
                    job_id,
                    status="failed",
                    progress=1.0,
                    message="Local job execution failed",
                    error=str(exc),
                )
        finally:
            with self._local_lock:
                self._local_running = max(0, self._local_running - 1)

    def _local_task_done(self, job_id: str, future) -> None:
        self._local_capacity.release()
        if future.cancelled():
            self.update(
                job_id,
                status="failed",
                progress=1.0,
                message="Local job cancelled during shutdown",
                error="Local job cancelled during shutdown",
            )

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting local fallback work and cancel jobs not yet started."""

        self._local_executor.shutdown(wait=wait, cancel_futures=True)

    def _mark_local_fallback_disabled(self, job_id: str, message: str) -> None:
        self.update(
            job_id,
            status="failed",
            progress=1.0,
            message=message,
            error=message,
        )
        self._logger.error(message)

    def _get_local_record(self, job_id: str) -> Optional[JobRecord]:
        with self._local_lock:
            record = self._local_records.get(job_id)
            return replace(record) if record is not None else None

    def _prune_local_records_unlocked(self) -> None:
        if len(self._local_records) <= self._local_record_limit:
            return
        target_size = max(1, int(self._local_record_limit * 0.9))
        terminal_records = sorted(
            (
                record
                for record in self._local_records.values()
                if record.status in {"succeeded", "failed"}
            ),
            key=lambda item: item.updated_at,
        )
        for record in terminal_records[: len(self._local_records) - target_size]:
            self._local_records.pop(record.id, None)

    def _can_try_redis(self) -> bool:
        return time.monotonic() >= self._redis_retry_after

    def _mark_redis_unavailable(self, exc: RedisError, operation: str) -> None:
        self._redis_retry_after = time.monotonic() + self._redis_retry_interval_seconds
        if not self._redis_unavailable:
            self._logger.warning(
                "Redis unavailable during %s; using in-memory/local fallback and retrying in %.0fs: %s",
                operation,
                self._redis_retry_interval_seconds,
                exc,
            )
        self._redis_unavailable = True

    def _mark_redis_available(self) -> None:
        self._redis_retry_after = 0.0
        if self._redis_unavailable:
            self._logger.info("Redis connection restored; resumed Redis-backed job state and queue")
        self._redis_unavailable = False
        self._local_queue_warning_logged = False

    def _registry_count(self, registry_name: str) -> int:
        registry = getattr(self.queue, registry_name, None)
        if registry is None:
            return 0
        count = getattr(registry, "count", 0)
        return int(count() if callable(count) else count)

    @staticmethod
    def _key(job_id: str) -> str:
        return f"myreid:jobs:{job_id}"


job_manager = RedisJobManager()
