import argparse
import base64
import json
import shutil

from . import config, runtime


def main():
    parser = argparse.ArgumentParser(description='Standalone camera discovery and RTSP gateway')
    parser.add_argument('action', choices=['init', 'scan', 'run', 'supervise', 'stop', 'stopped',
                                         'status', 'enable', 'disable', 'add-rtsp', 'probe', 'learn-placeholder'])
    parser.add_argument('--id', help='Stable source ID from scan/status')
    parser.add_argument('--url', help='Upstream RTSP URL; prefer editing sources.json for credentials')
    parser.add_argument('--label')
    args = parser.parse_args()
    try:
        if args.action in ('init', 'scan'):
            devices, settings = runtime.scan(shutil.which('ffmpeg'))
            runtime.initialize(settings)
            if args.action == 'scan':
                print(json.dumps(devices, ensure_ascii=False, indent=2))
        elif args.action in ('run', 'supervise'):
            getattr(runtime, args.action)()
        elif args.action == 'stop':
            config.RUNTIME.mkdir(parents=True, exist_ok=True)
            (config.RUNTIME / 'stop').touch()
        elif args.action == 'stopped':
            return 0 if runtime.stopped() else 1
        elif args.action == 'status':
            print(json.dumps(config.read_json(config.RUNTIME / 'registry.json'), ensure_ascii=False, indent=2))
        else:
            if not args.id:
                parser.error('--id is required')
            if args.action in ('probe', 'learn-placeholder'):
                source = next(s for s in config.load_config()['sources'] if s['id'] == args.id)
                result, frame = runtime.probe(source)
                if args.action == 'learn-placeholder':
                    if not frame:
                        raise ValueError('Cannot learn placeholder: no decoded frames')
                    def remember(settings):
                        item = next(s for s in settings['sources'] if s['id'] == args.id)
                        item['health'] = dict(reject_blank=True, unavailable_frame=base64.b64encode(frame).decode())
                    config.update_config(remember)
                print(json.dumps(result))
            else:
                def update(settings):
                    source = next((s for s in settings['sources'] if s['id'] == args.id), None)
                    if args.action == 'add-rtsp':
                        if source:
                            raise ValueError('Source ID already exists; edit sources.json to update it')
                        settings['sources'].append(dict(id=args.id, kind='rtsp', enabled=True,
                                                        label=args.label or args.id, url=args.url or ''))
                    elif source is None:
                        raise ValueError('Unknown source ID; run scan first')
                    else:
                        source['enabled'] = args.action == 'enable'
                config.update_config(update)
    except (ValueError, StopIteration, OSError) as exc:
        parser.error(str(exc) or 'Unknown source ID')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
