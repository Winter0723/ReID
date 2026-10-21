import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call

from camera_gateway import config, runtime, discovery


class GatewayTests(unittest.TestCase):
    def test_probe_uses_published_stream_even_for_rejected_placeholder(self):
        from types import SimpleNamespace
        source = dict(id='virtual', kind='dshow', device='virtual-slot', health=dict(reject_blank=True))
        with patch.object(runtime.shutil, 'which', return_value='ffmpeg'), \
                patch.object(runtime, 'read_json', return_value={'reader': 'test'}), \
                patch.object(runtime, 'ready_paths', return_value={'local/virtual'}), \
                patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(stdout=bytes(768 * 3))) as run:
            result, _ = runtime.probe(source)
        self.assertTrue(result['decodable'])
        self.assertEqual(result['signal'], 'unavailable')
        self.assertIn('rtsp://reader:test@127.0.0.1:18554/local/virtual', run.call_args.args[0])
        self.assertNotIn('video=virtual-slot', run.call_args.args[0])

    def test_api_reconciles_changes_when_file_notifications_are_missing(self):
        settings = config.default_config()
        settings['sources'] = [
            dict(id='keep', kind='dshow', device='usb#keep', enabled=True),
            dict(id='new', kind='rtsp', url='rtsp://camera/live', enabled=True),
            dict(id='change', kind='rtsp', url='rtsp://camera/new', enabled=True)]
        live = [dict(name='local/keep', source='publisher', overridePublisher=False),
                dict(name='local/remove', source='publisher'),
                dict(name='local/change', source='rtsp://camera/old'),
                dict(name='other-app', source='publisher')]
        with patch.object(runtime, 'api_items', return_value=live), patch.object(runtime, 'control_api') as api:
            runtime.synchronize_paths({}, settings)
        self.assertEqual(api.call_args_list, [
            call({}, 'config/paths/add/local/new', 'POST', dict(source='rtsp://camera/live', rtspTransport='tcp', sourceOnDemand=False)),
            call({}, 'config/paths/replace/local/change', 'POST', dict(source='rtsp://camera/new', rtspTransport='tcp', sourceOnDemand=False)),
            call({}, 'config/paths/delete/local/remove', 'DELETE')])

    def test_invalid_nested_config_is_rejected_without_crashing_agent(self):
        for update in [dict(discovery=[]), dict(capture=None), dict(capture=dict(fps=0)), dict(sources=[None])]:
            with self.assertRaises(ValueError):
                config.validate({**config.default_config(), **update})

    def test_multiple_usb_hotplug_persists_and_reuses_configuration(self):
        devices = discovery.parse_dshow('''"A" (video)
Alternative name "usb#first"
"B" (video)
Alternative name "usb#second"
"Software Camera" (video)
Alternative name "root#virtual"''')
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'RUNTIME', Path(folder)):
            first = config.reconcile(devices[:1])
            self.assertTrue(first['sources'][0]['enabled'])
            both = config.reconcile(devices)
            self.assertEqual(sum(s['enabled'] for s in both['sources']), 2)
            before = (Path(folder) / 'sources.json').read_bytes()
            config.reconcile([])
            config.reconcile(devices[::-1])
            self.assertEqual(before, (Path(folder) / 'sources.json').read_bytes())

    def test_network_sources_render_to_mediamtx_and_do_not_leak_upstream(self):
        settings = config.default_config()
        source = dict(id='gate', label='Gate', kind='rtsp', enabled=True,
                      url='rtsp://upstream:private@camera:554/live')
        settings['sources'] = [source]
        config.validate(settings)
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'RUNTIME', Path(folder)):
            runtime.initialize(settings)
            generated = config.read_json(Path(folder) / 'mediamtx.yml')
            self.assertEqual(generated['paths']['local/gate']['source'], source['url'])
            self.assertFalse(generated['paths']['local/gate']['sourceOnDemand'])
            self.assertNotIn('private', json.dumps(config.public_source(source)))
            source['enabled'] = False
            runtime.initialize(settings)
            self.assertEqual(config.read_json(Path(folder) / 'mediamtx.yml')['paths'], {})

    def test_bad_configuration_preserves_last_good_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'RUNTIME', Path(folder)):
            config.reconcile([])
            before = (Path(folder) / 'sources.json').read_bytes()
            for source in [dict(id='../bad', kind='rtsp', enabled=True, url='rtsp://camera/live'),
                           dict(id='bad', kind='rtsp', enabled=True, url='file:///etc/passwd')]:
                with self.assertRaises(ValueError):
                    config.update_config(lambda c: c['sources'].append(source))
                self.assertEqual(before, (Path(folder) / 'sources.json').read_bytes())

    def test_generic_configured_placeholder_and_static_video(self):
        reference = bytes([0] * 384 + [100] * 384)
        signal = runtime.VideoSignal(dict(reject_blank=True, unavailable_frame=base64.b64encode(reference).decode()))
        signal.observe(reference, 10)
        self.assertFalse(signal.available(10))
        real = bytes([50] * 768)  # static, uniform scene remains valid; no motion heuristics
        signal.observe(real, 11)
        self.assertFalse(signal.available(11))
        signal.observe(real, 12)
        self.assertTrue(signal.available(12))
        signal.observe(reference, 13)
        self.assertTrue(signal.available(13))  # tolerate transient placeholders
        self.assertFalse(signal.available(18))
        signal.observe(real, 19)
        signal.observe(real, 20)
        self.assertTrue(signal.available(20))
        self.assertFalse(runtime.VideoSignal({}).is_placeholder(bytes(768)))

    def test_gateway_runtime_has_no_backend_or_database_dependency(self):
        import inspect
        source = inspect.getsource(runtime)
        self.assertNotIn('production_running', source)
        self.assertNotIn('import database', source)
        self.assertNotIn('myreid-production-backend', source)


if __name__ == '__main__':
    unittest.main()
