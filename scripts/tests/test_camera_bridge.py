import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from camera_gateway import runtime as agent, config as gateway_config, discovery
bridge = load('bridge', 'FastAPIBackend/app/services/camera_bridge.py')


class BridgeTests(unittest.TestCase):
    def test_progress_distinguishes_duplicates_and_stalled_output(self):
        from types import SimpleNamespace
        from io import StringIO
        publisher = agent.Publisher.__new__(agent.Publisher)
        publisher.frames = publisher._metric_frames = 0
        publisher.last_progress = publisher._metric_at = 10.0
        publisher.metrics = dict(output_fps=0.0, nonduplicate_fps=0.0, dup_frames=0, drop_frames=0)
        publisher.process = SimpleNamespace(stdout=StringIO(
            'frame=30\ndup_frames=5\ndrop_frames=2\nprogress=continue\n'))
        with patch.object(agent.time, 'monotonic', return_value=11.0):
            publisher._progress()
        self.assertEqual(publisher.metrics, dict(output_fps=30.0, nonduplicate_fps=25.0,
                                                 dup_frames=5, drop_frames=2))
        publisher.process.stdout = StringIO('frame=bad\ndup_frames=bad\nprogress=continue\n')
        with patch.object(agent.time, 'monotonic', return_value=12.0):
            publisher._progress()
        self.assertEqual(publisher.metrics['output_fps'], 0.0)
        self.assertEqual(publisher.metrics['nonduplicate_fps'], 0.0)
        self.assertEqual(publisher.last_progress, 11.0)
        self.assertEqual(publisher.metrics['dup_frames'], 5)

    def test_negotiate_real_30fps_mode_before_scaling_output(self):
        from types import SimpleNamespace
        options = '''pixel_format=yuyv422 min s=640x480 fps=5 max s=640x480 fps=15
vcodec=mjpeg min s=640x480 fps=30 max s=640x480 fps=30
pixel_format=nv12 min s=1920x1080 fps=30 max s=1920x1080 fps=30'''
        with patch.object(agent.subprocess, 'run', return_value=SimpleNamespace(stderr=options)):
            self.assertEqual(agent.capture_options('ffmpeg', {'device': 'usb'}),
                ['-vcodec', 'mjpeg', '-video_size', '640x480', '-framerate', '30'])
        with patch.object(agent.subprocess, 'run', return_value=SimpleNamespace(stderr='')):
            self.assertEqual(agent.capture_options('ffmpeg', {'device': 'usb'}), [])
        with patch.object(agent.subprocess, 'run', return_value=SimpleNamespace(stderr='vcodec=mjpeg min s=640x480 fps=60 max s=640x480 fps=60')):
            self.assertEqual(agent.capture_options('ffmpeg', {'device': 'usb'})[-1], '60')
        with patch.object(agent.subprocess, 'run', side_effect=agent.subprocess.TimeoutExpired('ffmpeg', 15)):
            self.assertEqual(agent.capture_options('ffmpeg', {'device': 'usb'}), [])

    def test_device_inventory_is_generic_and_identity_is_stable(self):
        output = '''[dshow] "Iriun Webcam" (video)
[dshow] Alternative name "@device_pnp_root#virtual"
[dshow] "USB Camera" (video)
[dshow] Alternative name "@device_pnp_usb#vid_1234#serial"
[dshow] "Microphone" (audio)
[dshow] Alternative name "audio_device"
'''
        first = discovery.parse_dshow(output)
        second = discovery.parse_dshow(output.replace('USB Camera', 'Renamed Camera'))
        self.assertEqual(len(first), 2)
        self.assertFalse(first[0]['physical'])
        self.assertTrue(first[1]['physical'])
        self.assertEqual(first[1]['id'], second[1]['id'])
        self.assertEqual(second[1]['label'], 'Renamed Camera')

    def test_only_selected_iriun_channel_is_enabled_and_usb_stays_default(self):
        output = '''[dshow] "Iriun Webcam" (video)
[dshow] Alternative name "@device_pnp_root#phone1"
[dshow] "Iriun Webcam #2" (video)
[dshow] Alternative name "@device_pnp_root#phone2"
[dshow] "OBS Virtual Camera" (video)
[dshow] Alternative name "@device_sw_obs"
[dshow] "USB Camera" (video)
[dshow] Alternative name "@device_pnp_usb#serial"
[dshow] "Iriun Webcam" (audio)
[dshow] Alternative name "audio_device"
'''
        devices = discovery.parse_dshow(output)
        self.assertEqual(len(devices), 4)
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway_config, 'RUNTIME', Path(directory)):
            # Existing explicit selection migrates once; no vendor names in discovery policy.
            agent.write_json(Path(directory) / 'devices.json', {'enabled_iriun_labels': ['Iriun Webcam']})
            settings = gateway_config.reconcile(devices)
            self.assertEqual([d['label'] for d in settings['sources'] if d['enabled']], ['Iriun Webcam', 'USB Camera'])
            gateway_config.update_config(lambda c: c['sources'][3].update(enabled=False, label='Entrance'))
            gateway_config.reconcile([])  # unplug retains source identity/configuration
            settings = gateway_config.reconcile(devices)
            self.assertEqual(len(settings['sources']), 4)
            self.assertFalse(settings['sources'][3]['enabled'])
            self.assertEqual(settings['sources'][3]['label'], 'Entrance')

    def test_iriun_range_uses_30fps_and_upright_yuv_format(self):
        from types import SimpleNamespace
        options = '''pixel_format=bgr24 min s=640x480 fps=29.97 max s=640x480 fps=59.9999
pixel_format=yuyv422 min s=640x480 fps=29.97 max s=640x480 fps=59.9999'''
        with patch.object(agent.subprocess, 'run', return_value=SimpleNamespace(stderr=options)):
            self.assertEqual(agent.capture_options('ffmpeg', {'device': 'iriun'}),
                             ['-pixel_format', 'yuyv422', '-video_size', '640x480', '-framerate', '30'])

    def test_heartbeat_default_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, MYREID_CAMERA_BRIDGE_DIR=directory):
            path = Path(directory)
            source = 'rtsp://mediamtx:8554/local/abc'
            registry = dict(updated_at=time.time(), default_source=source,
                            devices=[dict(source=source, label='USB', online=True, status='online',
                                          output_fps=30, nonduplicate_fps=25)])
            agent.write_json(path / 'registry.json', registry)
            agent.write_json(path / 'credentials.json', {'reader': 'secret'})
            self.assertTrue(bridge.devices()[0]['online'])
            self.assertEqual(bridge.devices()[0]['nonduplicate_fps'], 25)
            self.assertEqual(bridge.resolve('0'), source)
            self.assertNotIn('secret', json.dumps(bridge.devices()))
            self.assertEqual(bridge.capture_url('0'), 'rtsp://reader:secret@mediamtx:8554/local/abc')
            self.assertEqual(bridge.capture_url('rtsp://other/live'), 'rtsp://other/live')
            registry['updated_at'] -= 21
            agent.write_json(path / 'registry.json', registry)
            self.assertFalse(bridge.devices()[0]['online'])
            self.assertEqual(bridge.devices()[0]['output_fps'], 0)
            self.assertEqual(bridge.devices()[0]['nonduplicate_fps'], 0)
            self.assertEqual(bridge.devices()[0]['status'], 'agent_offline')
            self.assertEqual(bridge.resolve('0'), source)

    def test_missing_or_partial_registry_is_safe(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, MYREID_CAMERA_BRIDGE_DIR=directory):
            self.assertEqual(bridge.devices(), [])
            (Path(directory) / 'registry.json').write_text('{')
            self.assertEqual(bridge.devices(), [])
            self.assertEqual(bridge.resolve('0'), '0')

    def test_initialization_preserves_secrets_and_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'RUNTIME', Path(directory)):
            settings = gateway_config.default_config()
            settings['sources'] = [dict(id='usb', kind='dshow', device='usb', enabled=True)]
            first = agent.initialize(settings)
            self.assertEqual(first, agent.initialize(settings))
            config = json.loads((Path(directory) / 'mediamtx.yml').read_text())
            roles = {role['user']: role for role in config['authInternalUsers']}
            self.assertEqual(roles['publisher']['permissions'], [{'action': 'publish', 'path': '~^local/'}])
            self.assertEqual(roles['reader']['permissions'], [{'action': 'read', 'path': '~^local/'}])
            self.assertFalse(config['paths']['local/usb']['overridePublisher'])


if __name__ == '__main__':
    unittest.main()
