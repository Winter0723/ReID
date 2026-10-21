"""Standalone local camera publishers and RTSP pull-source reconciliation."""
import base64
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

from .config import (RUNTIME, DEFAULT_CAPTURE, FileLock, load_config, reconcile,
                     public_source, read_json, write_json)
from .discovery import discover, FLAGS


def configured_paths(config):
    paths = {}
    for source in config['sources']:
        if not source['enabled']:
            continue
        path = 'local/' + source['id']
        if source['kind'] == 'rtsp':
            paths[path] = dict(source=source['url'], rtspTransport='tcp', sourceOnDemand=False)
        else:
            paths[path] = dict(source='publisher', overridePublisher=False)
    return paths


def initialize(config=None):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    credentials = read_json(RUNTIME / 'credentials.json')
    if not credentials:
        credentials = {role: secrets.token_hex(24) for role in ('publisher', 'reader', 'agent')}
        write_json(RUNTIME / 'credentials.json', credentials)
    config = config if config is not None else load_config()
    settings = {
        'rtspTransports': ['tcp'], 'rtmp': False, 'hls': False, 'webrtc': False, 'srt': False, 'moq': False,
        'api': True, 'authMethod': 'internal',
        'authInternalUsers': [
            {'user': role, 'pass': credentials[role], 'permissions': permissions}
            for role, permissions in (
                ('publisher', [{'action': 'publish', 'path': '~^local/'}]),
                ('reader', [{'action': 'read', 'path': '~^local/'}]),
                ('agent', [{'action': 'api'}]))],
        'paths': configured_paths(config),
    }
    path = RUNTIME / 'mediamtx.yml'
    if read_json(path) != settings:
        write_json(path, settings)
    return credentials


def control_api(credentials, path, method='GET', body=None):
    request = urllib.request.Request('http://127.0.0.1:19997/v3/' + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
    token = base64.b64encode(('agent:' + credentials['agent']).encode()).decode()
    request.add_header('Authorization', 'Basic ' + token)
    if body is not None:
        request.add_header('Content-Type', 'application/json')
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
        payload = response.read()
        return json.loads(payload) if payload else {}


def api_items(credentials, path):
    page = 0
    while True:
        result = control_api(credentials, f'{path}?itemsPerPage=1000&page={page}')
        yield from result.get('items', [])
        page += 1
        if page >= result.get('pageCount', 1):
            break


def synchronize_paths(credentials, config):
    """Reconcile the live server even when Docker bind-mount notifications are lost.

    Files persist desired state across server restarts; API updates affect only
    changed paths. Unrelated manually managed paths are left alone.
    """
    desired = configured_paths(config)
    current = {p['name']: p for p in api_items(credentials, 'config/paths/list')}
    for name, settings in desired.items():
        if name not in current:
            control_api(credentials, 'config/paths/add/' + name, 'POST', settings)
        elif any(current[name].get(key) != value for key, value in settings.items()):
            control_api(credentials, 'config/paths/replace/' + name, 'POST', settings)
    for name in current:
        if name.startswith('local/') and name not in desired:
            control_api(credentials, 'config/paths/delete/' + name, 'DELETE')


def ready_paths(credentials):
    return {item['name'] for item in api_items(credentials, 'paths/list') if item.get('ready')}


class VideoSignal:
    """Optional, source-configured placeholder check; never infer disconnect from motion.

    Virtual drivers can keep producing frames without a camera. A known unavailable
    image can be configured per source; there are no vendor names or images in code.
    """
    FRAME_BYTES = 32 * 24

    def __init__(self, policy):
        self.reference = base64.b64decode(policy['unavailable_frame']) if policy.get('unavailable_frame') else None
        self.reject_blank = policy.get('reject_blank', False)
        self.last_video_at = None
        self.good_samples = 0

    def is_placeholder(self, frame):
        if len(frame) != self.FRAME_BYTES:
            return True
        if self.reject_blank and max(frame) <= 2:
            return True
        if not self.reference:
            return False
        foreground = [abs(a-b) for a,b in zip(frame, self.reference) if b > 10]
        background = [abs(a-b) for a,b in zip(frame, self.reference) if b <= 10]
        return bool(foreground and background and sum(foreground)/len(foreground) < 12
                    and sum(background)/len(background) < 3)

    def observe(self, frame, now):
        if self.is_placeholder(frame):
            self.good_samples = 0
        else:
            self.good_samples += 1
            if self.good_samples >= 2:
                self.last_video_at = now

    def available(self, now):
        return self.last_video_at is not None and now-self.last_video_at < 5


def capture_options(ffmpeg, device):
    """Choose an advertised native capture mode close to the configured output."""
    if device.get("kind", "dshow") != "dshow":
        return []
    capture = {**DEFAULT_CAPTURE, **device.get("capture", {})}
    desired_fps = capture["fps"]
    try:
        result = subprocess.run([ffmpeg, '-hide_banner', '-list_options', 'true', '-f', 'dshow',
                                 '-i', 'video=' + device['device']], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=15, creationflags=FLAGS)
    except subprocess.TimeoutExpired:
        logging.warning('Camera mode discovery timed out; using driver defaults')
        return []
    modes = []
    for line in result.stderr.splitlines():
        fmt = re.search(r'(pixel_format|vcodec)=(\w+)', line)
        if not fmt:
            continue
        bounds = re.findall(r's=(\d+)x(\d+) fps=([\d.]+)', line)
        if len(bounds) == 2 and bounds[0][:2] == bounds[1][:2]:
            # Use the requested rate within a continuous advertised range.
            low, high = float(bounds[0][2]), float(bounds[1][2])
            if low <= desired_fps <= high:
                bounds = [(*bounds[0][:2], str(desired_fps))]
        for width, height, fps in bounds:
            w, h, rate = int(width), int(height), float(fps)
            if w >= 320 and h >= 240 and rate >= desired_fps:
                # Prefer native YUV when otherwise equal; some RGB drivers
                # expose bottom-up buffers with inconsistent orientation metadata.
                modes.append((abs(w - capture["width"]) + abs(h - capture["height"]), fmt.group(1) != 'vcodec',
                              fmt.group(2) in ('bgr24', 'rgb24'), rate, w, h, fmt.groups()))
    if not modes:
        return []  # Preserve support for drivers without a standard mode list.
    _, _, _, rate, width, height, (kind, value) = min(modes)
    return ['-' + kind, value, '-video_size', f'{width}x{height}', '-framerate', f'{rate:g}']


class Publisher:
    def __init__(self, ffmpeg, device, credentials):
        self.last_progress = time.monotonic()
        self.frames = 0
        self.metrics = dict(output_fps=0.0, nonduplicate_fps=0.0, dup_frames=0, drop_frames=0)
        self._metric_at = self.last_progress
        self._metric_frames = 0
        self.signal = VideoSignal(device["health"]) if device.get("health") else None
        capture = {**DEFAULT_CAPTURE, **device.get("capture", {})}
        target = f"rtsp://publisher:{credentials['publisher']}@127.0.0.1:18554/local/{device['id']}"
        # Prefer a native 640/30 mode, then normalize without a large latency buffer.
        options = capture_options(ffmpeg, device)
        args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-f', device['kind'], '-rtbufsize', '8M', *options, '-i',
                ('video=' if device['kind'] == 'dshow' else '') + device['device'],
                '-map', '0:v:0', '-an', '-vf', f"scale={capture['width']}:{capture['height']}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                '-r', str(capture['fps']), '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
                '-pix_fmt', 'yuv420p', '-g', str(capture['fps']), '-bf', '0', '-b:v', str(capture['bitrate_kbps']) + 'k',
                '-progress', 'pipe:2' if self.signal else 'pipe:1', '-nostats', '-rtsp_transport', 'tcp', '-f', 'rtsp', target]
        if self.signal:
            # Inspect one tiny frame per second from the SAME DirectShow capture.
            args += ['-map', '0:v:0', '-an', '-vf', 'fps=1,scale=32:24,format=gray',
                     '-c:v', 'rawvideo', '-f', 'rawvideo', 'pipe:1']
        output_mode = {} if self.signal else dict(text=True, encoding='utf-8', errors='replace')
        self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        **output_mode, creationflags=FLAGS)
        if self.signal:
            threading.Thread(target=self._signal_frames, daemon=True).start()
            threading.Thread(target=self._progress, args=(self.process.stderr, credentials), daemon=True).start()
        else:
            threading.Thread(target=self._progress, daemon=True).start()
            threading.Thread(target=self._errors, args=(credentials,), daemon=True).start()

    def _signal_frames(self):
        pending = b''
        while True:
            chunk = self.process.stdout.read(self.signal.FRAME_BYTES - len(pending))
            if not chunk:
                return
            pending += chunk
            if len(pending) == self.signal.FRAME_BYTES:
                self.signal.observe(pending, time.monotonic())
                pending = b''

    def _progress(self, lines=None, credentials=None):
        pending = {}
        for line in self.process.stdout if lines is None else lines:
            if isinstance(line, bytes):
                line = line.decode('utf-8', errors='replace')
            key, separator, value = line.strip().partition('=')
            if not separator:
                if credentials and line.strip():
                    for secret in credentials.values():
                        line = line.replace(secret, '[redacted]')
                    logging.warning('ffmpeg: %s', line.rstrip())
                continue
            if key in ('frame', 'dup_frames', 'drop_frames'):
                try:
                    pending[key] = max(0, int(value))
                except ValueError:
                    pass
            elif key == 'progress':
                now = time.monotonic()
                frames = pending.get('frame', self.frames)
                duplicates = pending.get('dup_frames', self.metrics['dup_frames'])
                elapsed = now - self._metric_at
                if frames > self.frames:
                    self.frames, self.last_progress = frames, now
                if elapsed > 0:
                    delivered = max(0, frames - self._metric_frames)
                    repeated = max(0, duplicates - self.metrics['dup_frames'])
                    # This estimates new encoded frames, not sensor capture rate.
                    self.metrics = dict(
                        output_fps=round(delivered / elapsed, 2),
                        nonduplicate_fps=round(max(0, delivered - repeated) / elapsed, 2),
                        dup_frames=duplicates,
                        drop_frames=pending.get('drop_frames', self.metrics['drop_frames']))
                    self._metric_at, self._metric_frames = now, frames
                pending.clear()

    def _errors(self, credentials):
        for line in self.process.stderr:
            for secret in credentials.values():
                line = line.replace(secret, '[redacted]')
            logging.warning('ffmpeg: %s', line.rstrip())

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def own_child_processes():
    """Windows closes the job on agent death, releasing every camera publisher."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                    ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                    ('scheduling', wintypes.DWORD)]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle  # Intentionally kept open for the lifetime of this process.


def scan(ffmpeg):
    devices = discover(ffmpeg)
    config = reconcile(devices)
    write_json(RUNTIME / 'inventory.json', dict(updated_at=time.time(), devices=devices))
    return devices, config


def run():
    ffmpeg = shutil.which('ffmpeg')
    if os.name == 'nt' and not ffmpeg:
        raise SystemExit('FFmpeg is required for Windows camera discovery/capture')
    try:
        lock = FileLock(RUNTIME / 'agent.lock').__enter__()
    except OSError:
        return
    job_handle = own_child_processes()
    handler = RotatingFileHandler(RUNTIME / 'agent.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    shutdown = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *args: shutdown.set())
    publishers, retry_at, signatures = {}, {}, {}
    config = load_config()
    credentials = initialize(config)
    previous = read_json(RUNTIME / 'registry.json')
    default_source = previous.get('default_source', '')
    next_scan, present = 0, {}
    logging.info('Independent camera gateway agent started pid=%s', os.getpid())
    try:
        while not shutdown.is_set() and not (RUNTIME / 'stop').exists():
            now = time.monotonic()
            try:
                config = load_config()
                if now >= next_scan:
                    found, config = scan(ffmpeg)
                    present = {device['id']: device for device in found}
                    next_scan = time.monotonic() + config.get('discovery', {}).get('scan_interval_seconds', 3)
                initialize(config)  # Persist for server restart; the API also applies live changes.
            except (OSError, ValueError, subprocess.TimeoutExpired):
                logging.exception('Discovery/config update failed; retaining last valid configuration')
                next_scan = time.monotonic() + 3
            sources = {source['id']: source for source in config['sources']}
            if not default_source:
                enabled = [s for s in sources.values() if s['enabled']]
                preferred = [s for s in enabled if s.get('physical')]
                if preferred or enabled:
                    default_source = public_source((preferred or enabled)[0])['source']
            try:
                ready, server_up = ready_paths(credentials), True
            except Exception:
                ready, server_up = set(), False
            if server_up:
                try:
                    synchronize_paths(credentials, config)
                except (OSError, ValueError):
                    logging.exception('RTSP path update failed; retrying without interrupting other sources')
            wanted = {key: {**source, 'capture': {**config.get('capture', {}), **source.get('capture', {})}}
                      for key, source in sources.items()
                      if source['enabled'] and source['kind'] != 'rtsp' and key in present}
            now = time.monotonic()
            for key, publisher in list(publishers.items()):
                if (not server_up or key not in wanted or signatures[key] != json.dumps(wanted[key], sort_keys=True)
                        or publisher.process.poll() is not None or now-publisher.last_progress > 20):
                    publisher.stop()
                    del publishers[key]
                    retry_at[key] = now + 2
            if server_up:
                for key, device in wanted.items():
                    if key not in publishers and now >= retry_at.get(key, 0):
                        try:
                            publishers[key] = Publisher(ffmpeg, device, credentials)
                            signatures[key] = json.dumps(device, sort_keys=True)
                            logging.info('Started source %s (%s)', key, device['kind'])
                        except (OSError, subprocess.TimeoutExpired):
                            logging.exception('Cannot start source %s', key)
                            retry_at[key] = time.monotonic() + 5
            devices, now = [], time.monotonic()
            for key, source in sources.items():
                publisher = publishers.get(key)
                publishing = bool(publisher and publisher.frames > 0 and now-publisher.last_progress < 10)
                no_signal = bool(publishing and publisher.signal and not publisher.signal.available(now))
                online = bool(source['enabled'] and f'local/{key}' in ready and
                              (source['kind'] == 'rtsp' or (publishing and not no_signal)))
                status = ('disabled' if not source['enabled'] else 'online' if online else
                          'gateway_offline' if not server_up else
                          'source_unavailable' if source['kind'] == 'rtsp' else
                          'device_offline' if key not in present else
                          'no_video_signal' if no_signal else 'connecting_or_device_busy')
                item = {**public_source(source), 'online': online, 'status': status,
                        'index': len(devices), 'width': 0, 'height': 0,
                        'frames': publisher.frames if publisher else 0}
                item.update(publisher.metrics if publisher else {})
                if not online:
                    item.update(output_fps=0.0, nonduplicate_fps=0.0)
                devices.append(item)
            write_json(RUNTIME / 'registry.json', dict(updated_at=time.time(), pid=os.getpid(),
                       default_source=default_source, devices=devices))
            shutdown.wait(1)
    finally:
        for publisher in publishers.values():
            publisher.stop()
        registry = read_json(RUNTIME / 'registry.json')
        for device in registry.get('devices', []):
            device.update(online=False, status='agent_stopped', output_fps=0.0, nonduplicate_fps=0.0)
        registry['updated_at'] = time.time()
        write_json(RUNTIME / 'registry.json', registry)
        lock.__exit__()


def stopped():
    for name in ('agent.lock', 'supervisor.lock'):
        try:
            with FileLock(RUNTIME / name):
                pass
        except OSError:
            return False
    return True


def supervise():
    import sys
    try:
        lock = FileLock(RUNTIME / 'supervisor.lock').__enter__()
    except OSError:
        return
    job_handle = own_child_processes()
    try:
        while not (RUNTIME / 'stop').exists():
            with (RUNTIME / 'supervisor.log').open('w', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-m', 'camera_gateway', 'run'],
                    cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=log, creationflags=FLAGS)
                try:
                    child.wait()
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=10)
            if not (RUNTIME / 'stop').exists():
                time.sleep(3)
    finally:
        lock.__exit__()


def probe(source, seconds=3):
    """Decode bounded samples. Decodable virtual-driver output is not physical presence."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('FFmpeg is required to decode video samples')
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin']
    credentials = read_json(RUNTIME / 'credentials.json')
    try:
        publishing = f"local/{source['id']}" in ready_paths(credentials)
    except (KeyError, OSError, ValueError):
        publishing = False
    if publishing:
        secret = credentials['reader']
        args += ['-rtsp_transport', 'tcp', '-i', f"rtsp://reader:{secret}@127.0.0.1:18554/local/{source['id']}"]
    elif source['kind'] == 'rtsp':
        args += ['-rtsp_transport', 'tcp', '-i', source['url']]
    else:
        args += ['-f', source['kind'], *capture_options(ffmpeg, source), '-i',
                 ('video=' if source['kind'] == 'dshow' else '') + source['device']]
    args += ['-an', '-vf', 'fps=1,scale=32:24,format=gray', '-frames:v', str(seconds), '-f', 'rawvideo', '-']
    try:
        result = subprocess.run(args, capture_output=True, timeout=seconds + 15, creationflags=FLAGS)
    except subprocess.TimeoutExpired:
        return dict(id=source['id'], decodable=False, frames=0, signal='timeout'), b''
    frames = [result.stdout[i:i+768] for i in range(0, len(result.stdout), 768) if len(result.stdout[i:i+768]) == 768]
    signal_check = VideoSignal(source.get('health', {}))
    live = any(not signal_check.is_placeholder(frame) for frame in frames)
    return dict(id=source['id'], decodable=bool(frames), frames=len(frames),
                signal=('no_frames' if not frames else 'unknown' if not source.get('health')
                        else 'available' if live else 'unavailable')), (frames[-1] if frames else b'')
