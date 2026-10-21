"""Versioned gateway configuration. No database or application dependencies."""
import base64
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.getenv('CAMERA_GATEWAY_DIR', str(ROOT / 'tmp' / 'camera-bridge'))).resolve()
DEFAULT_CAPTURE = dict(width=640, height=480, fps=30, bitrate_kbps=1500)


class FileLock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise
        return self

    def __exit__(self, *args):
        self.file.close()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.1)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def default_config():
    return dict(version=1, discovery=dict(auto_enable_physical=True, scan_interval_seconds=3),
                capture=dict(DEFAULT_CAPTURE), sources=[])


def validate(config):
    if not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('sources'), list):
        raise ValueError('sources.json requires version=1 and a sources array')
    discovery = config.get('discovery', {})
    if not isinstance(discovery, dict):
        raise ValueError('discovery must be an object')
    if not isinstance(discovery.get('auto_enable_physical', True), bool):
        raise ValueError('auto_enable_physical must be boolean')
    interval = discovery.get('scan_interval_seconds', 3)
    if not isinstance(interval, (int, float)) or not 1 <= interval <= 60:
        raise ValueError('scan_interval_seconds must be between 1 and 60')
    ids = set()
    capture_limits = [('width', 160, 3840), ('height', 120, 2160), ('fps', 1, 60), ('bitrate_kbps', 100, 20000)]
    def validate_capture(capture):
        if not isinstance(capture, dict):
            raise ValueError('capture must be an object')
        for name, low, high in capture_limits:
            value = capture.get(name, DEFAULT_CAPTURE[name])
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'Invalid capture {name}')
    validate_capture(config.get('capture', {}))
    for source in config['sources']:
        if not isinstance(source, dict):
            raise ValueError('Each source must be an object')
        key = source.get('id', '')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key) or key in ids:
            raise ValueError('Every source requires a unique alphanumeric/dash/underscore id')
        ids.add(key)
        kind = source.get('kind')
        if kind not in ('dshow', 'v4l2', 'rtsp') or not isinstance(source.get('enabled'), bool):
            raise ValueError(f'Source {key}: invalid kind or enabled flag')
        if kind == 'rtsp':
            address = source.get('url', '')
            if not isinstance(address, str) or urlsplit(address).scheme not in ('rtsp', 'rtsps') or not urlsplit(address).hostname:
                raise ValueError(f'Source {key}: an rtsp:// or rtsps:// URL is required')
        elif not isinstance(source.get('device'), str) or not source['device']:
            raise ValueError(f'Source {key}: a stable device path is required')
        validate_capture(source.get('capture', {}))
        health = source.get('health', {})
        if not isinstance(health, dict) or not isinstance(health.get('reject_blank', False), bool):
            raise ValueError(f'Source {key}: health must be an object with a boolean reject_blank')
        if 'unavailable_frame' in health:
            try:
                frame = base64.b64decode(health['unavailable_frame'], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Source {key}: invalid unavailable_frame') from exc
            if len(frame) != 32 * 24:
                raise ValueError(f'Source {key}: unavailable_frame must contain 32x24 grayscale bytes')
    return config


def load_config():
    path = RUNTIME / 'sources.json'
    if not path.exists():
        return default_config()
    # Never silently replace a malformed operator configuration with defaults.
    return validate(json.loads(path.read_text(encoding='utf-8-sig')))


def update_config(mutate):
    import time
    for attempt in range(30):
        try:
            with FileLock(RUNTIME / 'config.lock'):
                config = load_config()
                before = json.dumps(config, sort_keys=True)
                mutate(config)
                validate(config)
                if not (RUNTIME / 'sources.json').exists() or before != json.dumps(config, sort_keys=True):
                    write_json(RUNTIME / 'sources.json', config)
                return config
        except OSError:
            if attempt == 29:
                raise
            time.sleep(.1)


def reconcile(discovered):
    """Add new devices; never overwrite enabled, labels or tuning chosen by users."""
    legacy = read_json(RUNTIME / 'devices.json')
    migrate = not (RUNTIME / 'sources.json').exists()
    def merge(config):
        existing = {source['id'] for source in config['sources']}
        for device in discovered:
            if device['id'] in existing:
                continue
            enabled = bool(device['physical'] and config.get('discovery', {}).get('auto_enable_physical', True))
            # One-time migration of the old explicit selection, not a driver policy.
            if migrate and device['label'] in legacy.get('enabled_iriun_labels', []):
                enabled = True
            config['sources'].append({**device, 'enabled': enabled})
            existing.add(device['id'])
    return update_config(merge)


def public_source(source):
    return {'id': source['id'], 'label': source.get('label', source['id']),
            'source': f"rtsp://mediamtx:8554/local/{source['id']}", 'type': 'camera',
            'kind': source['kind'], 'enabled': source['enabled']}
