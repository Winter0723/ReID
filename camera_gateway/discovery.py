"""Host adapters enumerate device capabilities without opening camera streams."""
import hashlib
import os
from pathlib import Path
import re
import subprocess

FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def parse_dshow(output):
    devices, label = [], None
    for line in output.splitlines():
        match = re.search(r'"(.*)" \((video|audio|none)\)', line)
        if match:
            label = match[1] if match[2] == 'video' else None
        elif 'Alternative name "' in line and label:
            identity = line.split('Alternative name "', 1)[1].rsplit('"', 1)[0]
            physical = any(bus in identity.lower() for bus in ('usb#', 'pci#', 'acpi#'))
            devices.append(dict(id=hashlib.sha256(identity.encode()).hexdigest()[:16],
                                label=label, device=identity, kind='dshow', physical=physical))
            label = None
    return devices


def discover(ffmpeg):
    if os.name == 'nt':
        if not ffmpeg:
            raise ValueError('FFmpeg is required for Windows camera discovery')
        result = subprocess.run([ffmpeg, '-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=15,
                                creationflags=FLAGS)
        return parse_dshow(result.stderr)
    if os.name == 'posix' and Path('/sys/class/video4linux').exists():
        return discover_v4l2()
    return []  # RTSP-only operation works even without a local capture adapter.


def discover_v4l2():
    import fcntl
    import struct
    aliases = {path.resolve(): path for path in sorted(Path('/dev/v4l/by-id').glob('*'))}
    devices = []
    for sys_device in sorted(Path('/sys/class/video4linux').glob('video*')):
        node = Path('/dev') / sys_device.name
        try:
            # QUERYCAP distinguishes capture endpoints from metadata-only nodes.
            with node.open('rb', buffering=0) as camera:
                data = bytearray(104)
                fcntl.ioctl(camera, 0x80685600, data, True)
            capabilities, device_caps = struct.unpack_from('II', data, 84)
            caps = device_caps if capabilities & 0x80000000 else capabilities
            if not caps & (0x1 | 0x1000):
                continue
            stable = str(aliases.get(node.resolve(), sys_device.resolve()))
            devices.append(dict(id=hashlib.sha256(stable.encode()).hexdigest()[:16],
                                label=(sys_device / 'name').read_text().strip(),
                                device=str(aliases.get(node.resolve(), node)), kind='v4l2',
                                physical='/virtual/' not in str(sys_device.resolve())))
        except (OSError, ValueError):
            continue
    return devices
