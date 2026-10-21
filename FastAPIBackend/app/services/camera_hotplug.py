"""Optional backend consumer of the independent gateway's public device registry."""
import logging
import threading
import time
from urllib.parse import urlsplit

import scenic_database as database
from app.core.config import get_settings
from app.services import camera_bridge

logger = logging.getLogger('myreid.camera.hotplug')
_LOCK = threading.RLock()
_PAUSED_PROJECTS = set()


def pause_project(project_id):
    with _LOCK:
        _PAUSED_PROJECTS.add(project_id)


def resume_project(project_id):
    with _LOCK:
        _PAUSED_PROJECTS.discard(project_id)


def discover_points(db, project_id, devices):
    """Reuse aliases and disabled points; commit under the single-process discovery lock."""
    with _LOCK:
        points = db.query(database.ScenicCameraPoint).filter_by(project_id=project_id).all()
        sources = {camera_bridge.resolve(p.camera_source or p.camera_id or '0') for p in points}
        names = {p.name for p in points}
        has_entry = any(p.role == 'entry' for p in points)
        created = []
        for device in devices:
            source = str(device['source'])
            canonical = camera_bridge.resolve(source)
            if canonical in sources:
                continue
            base = str(device.get('label') or 'Camera')[:100]
            name, index = base, 2
            while name in names:
                name = f'{base} ({index})'
                index += 1
            point = database.ScenicCameraPoint(project_id=project_id, name=name,
                camera_source=source, camera_id=source, role='play' if has_entry else 'entry',
                location='', is_active=True)
            db.add(point)
            created.append(point)
            names.add(name)
            sources.add(canonical)
            has_entry = True
        if created:
            db.commit()
        return len(created)


class ScenicCameraHotplug:
    def __init__(self, interval_seconds=3):
        self.interval = interval_seconds
        self.next_tick = 0
        self.started = set()
        self.stopping = set()

    def tick(self):
        if (not camera_bridge.enabled()
                or not get_settings().scenic_camera_auto_start
                or time.monotonic() < self.next_tick):
            return
        self.next_tick = time.monotonic() + self.interval
        from app.services.camera_stream import scenic_camera_stream_manager as manager, _MAINTENANCE_STOP
        devices = [d for d in camera_bridge.devices() if d.get('online') and d.get('enabled', True)]
        available = {d['source'] for d in devices}
        with database.SessionLocal() as db:
            for project in db.query(database.Project).all():
                if _MAINTENANCE_STOP.is_set():
                    return
                discover_points(db, project.id, devices)
                points = db.query(database.ScenicCameraPoint).filter_by(project_id=project.id).all()
                seen = set()
                for point in points:
                    source = point.camera_source or point.camera_id or '0'
                    canonical = camera_bridge.resolve(source)
                    # External manually configured cameras keep their existing lifecycle.
                    if not (source.isdigit() or urlsplit(source).hostname == 'mediamtx'):
                        continue
                    key = project.id * 1_000_000 + point.id
                    identity = (key, canonical)
                    unavailable = canonical not in available or not point.is_active
                    if unavailable:
                        if manager.is_running(project.id, stream_key=key):
                            manager.stop(project.id, wait=False, stream_key=key)
                            self.stopping.add(key)
                        self.started.discard(identity)
                        continue
                    if canonical in seen:
                        continue
                    seen.add(canonical)
                    with _LOCK:
                        if project.id in _PAUSED_PROJECTS or _MAINTENANCE_STOP.is_set():
                            continue
                        if key in self.stopping:
                            if manager.is_running(project.id, stream_key=key):
                                continue
                            self.stopping.discard(key)
                        if manager.is_running(project.id, stream_key=key):
                            self.started.add(identity)
                            continue
                        if identity in self.started:
                            continue  # Respect manual stop until a real reconnect.
                        try:
                            manager.start(project_id=project.id, username=project.owner_username,
                                          camera_source=source, stream_key=key)
                            self.started.add(identity)
                        except Exception:
                            logger.exception('Camera source not ready; retrying', extra={'point_id': point.id})
