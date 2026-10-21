import logging
import importlib.util
import os
import time
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import database
import scenic_database
from app.services.camera_stream import camera_stream_manager, scenic_camera_stream_manager, reconcile_orphaned_camera_streams, start_camera_recording_maintenance, stop_camera_recording_maintenance
from app.services.central_camera_service import central_camera_service, stop_camera_services
from app.services.job_manager import job_manager
from app.services.model_runtime import DEVICE, load_models
from app.services.vector_store import scenic_vector_store, vector_store
from app.api.routes import admin, auth, health, projects, search, realtime
from app.api.scenic import router as scenic_router
from app.core.config import get_settings
from app.services.asset_delivery import has_valid_asset_signature
from app.core.observability import (
    begin_request,
    clear_request,
    configure_logging,
    error_tracker,
    route_template,
    runtime_metrics,
)

logger = logging.getLogger("myreid.api")
BASE_GALLERY_DIR = str(get_settings().gallery_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    if importlib.util.find_spec("lap") is None:
        raise RuntimeError("实时 ByteTrack 缺少 lap，请安装后端 requirements.txt")
    try:
        logger.info("正在初始化数据库", extra={"event": "startup.database"})
        database.init_database()
        scenic_database.init_database()
        from app.services.visitor_configuration import initialize_missing_visitor_services
        initialize_missing_visitor_services()

        # In-process ingestion dies with the API even when auto-start is disabled.
        reconciled_core = reconcile_orphaned_camera_streams(database)
        reconciled_scenic = reconcile_orphaned_camera_streams(scenic_database)
        if any(reconciled_core.values()) or any(reconciled_scenic.values()):
            logger.warning(
                "已清理异常退出遗留的相机任务",
                extra={
                    "event": "startup.camera_reconciled",
                    "core_media": reconciled_core["media"],
                    "core_jobs": reconciled_core["jobs"],
                    "scenic_media": reconciled_scenic["media"],
                    "scenic_jobs": reconciled_scenic["jobs"],
                },
            )
        if settings.camera_auto_start:
            logger.info("正在启动统一监控摄像头服务", extra={"event": "startup.central_camera"})
            central_camera_service.start()

        logger.info("正在初始化向量检索后端", extra={"event": "startup.vector_store"})
        vector_store.ensure_ready()
        scenic_vector_store.ensure_ready()

        os.makedirs(BASE_GALLERY_DIR, exist_ok=True)
        # Capture has its own threads; start it while model weights load.
        # Ingestion waits on the model services' existing initialization locks.
        start_camera_recording_maintenance()
        logger.info("正在加载 ReID 模型服务", extra={"event": "startup.models", "device": str(DEVICE)})
        model_load_started = time.perf_counter()
        load_models()
        runtime_metrics.set_model_load_seconds("all", time.perf_counter() - model_load_started)

        logger.info("服务启动完成", extra={"event": "startup.ready"})
        realtime.manager.start()
        yield
    finally:
        logger.info("正在停止后台服务", extra={"event": "shutdown.services"})
        realtime.manager.close()
        stop_camera_recording_maintenance()
        try:
            camera_stream_manager.stop_all(wait=False)
            scenic_camera_stream_manager.stop_all(wait=False)
            camera_stream_manager.stop_all(timeout=6)
            scenic_camera_stream_manager.stop_all(timeout=6)
            stop_camera_services()
        except Exception:
            logger.exception("停止相机服务失败", extra={"event": "shutdown.camera_error"})
        try:
            job_manager.shutdown(wait=False)
        except Exception:
            logger.exception("停止本地任务执行器失败", extra={"event": "shutdown.job_manager_error"})
        logger.info("服务关闭", extra={"event": "shutdown"})


async def request_observability_middleware(request: Request, call_next):
    request_id = begin_request(request.headers.get("X-Request-ID"))
    started = time.perf_counter()
    method = request.method
    try:
        response = await call_next(request)
    except Exception as exc:
        templated_path = route_template(request)
        error_id = error_tracker.record_exception(exc, method=method, path=templated_path, status_code=500)
        runtime_metrics.observe_request(
            method=method,
            path=templated_path,
            status_code=500,
            duration_seconds=time.perf_counter() - started,
        )
        logger.exception(
            "request failed",
            extra={
                "event": "http.request.error",
                "http_method": method,
                "http_path": templated_path,
                "status_code": 500,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "error_id": error_id,
            },
        )
        clear_request()
        raise

    templated_path = route_template(request)
    duration = time.perf_counter() - started
    runtime_metrics.observe_request(
        method=method,
        path=templated_path,
        status_code=response.status_code,
        duration_seconds=duration,
    )
    response.headers["X-Request-ID"] = request_id
    if response.status_code >= 500:
        error_id = error_tracker.record_http_error(method=method, path=templated_path, status_code=response.status_code)
        response.headers["X-Error-ID"] = error_id
    logger.info(
        "request completed",
        extra={
            "event": "http.request",
            "http_method": method,
            "http_path": templated_path,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        },
    )
    clear_request()
    return response


class CameraGalleryFiles(StaticFiles):
    """Live JPEGs are immutable response snapshots, even during atomic rollover."""

    async def get_response(self, path, scope):
        if not has_valid_asset_signature(Request(scope)):
            raise HTTPException(403, "媒体链接无效或已过期，请刷新页面")
        live = Path(path).name == "latest.jpg"
        if live:
            # Live frames must not return a cached 304 based on a previous inode.
            scope = {**scope, "headers": [(key, value) for key, value in scope["headers"]
                if key.lower() not in {b"if-none-match", b"if-modified-since"}]}
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "private, no-store"
        if not live or not isinstance(response, FileResponse):
            return response
        try:
            content = await run_in_threadpool(Path(response.path).read_bytes)
        except FileNotFoundError as exc:
            raise HTTPException(404, "相机画面暂不可用") from exc
        # StaticFiles stats then later opens the path. A new JPEG in between can
        # have another size; compute headers and body from the same opened file.
        return Response(content if scope["method"] != "HEAD" else b"", media_type="image/jpeg",
            headers={"Cache-Control": "no-store", "Content-Length": str(len(content))})


def mount_static(app: FastAPI) -> None:
    os.makedirs(BASE_GALLERY_DIR, exist_ok=True)
    app.mount("/gallery", CameraGalleryFiles(directory=BASE_GALLERY_DIR), name="gallery")
    settings = get_settings()
    if settings.object_storage_mode.lower() == "local":
        settings.object_storage_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/objects", CameraGalleryFiles(directory=str(settings.object_storage_dir)), name="objects")


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.middleware("http")(request_observability_middleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(projects.router)
    app.include_router(search.router)
    app.include_router(realtime.router)
    app.include_router(scenic_router)
    mount_static(app)
    return app
