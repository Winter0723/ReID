"""Read-only benchmark for a supplied video/reference and optional Market sample."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'FastAPIBackend'))


def market_sample(root, identities=24):
    import numpy as np
    from PIL import Image
    from app.services.model_runtime import clip_image_features
    pairs, used = [], set()
    for query in sorted((root/'query').glob('*.jpg')):
        identity, camera = query.stem.split('_')[:2]
        if identity in used or int(identity) <= 0:
            continue
        gallery = next((p for p in sorted((root/'bounding_box_test').glob(identity+'_*.jpg'))
                        if p.stem.split('_')[1][:2] != camera[:2]), None)
        if gallery:
            pairs.append((query, gallery)); used.add(identity)
        if len(pairs) >= identities:
            break
    features = []
    paths = [p for pair in pairs for p in pair]
    for offset in range(0, len(paths), 8):
        images = []
        for path in paths[offset:offset+8]:
            with Image.open(path) as image:
                images.append(image.convert('RGB'))
        features.extend(clip_image_features(images).detach().float().cpu().numpy())
    q, g = np.asarray(features)[::2], np.asarray(features)[1::2]
    scores = q @ g.T
    positive = scores.diagonal()
    negative = scores[~np.eye(len(pairs), dtype=bool)]
    return {'identities':len(pairs), 'positive_pairs':len(positive), 'negative_pairs':len(negative),
            'positive_quantiles':np.quantile(positive,[0,.1,.5,.9,1]).tolist(),
            'negative_quantiles':np.quantile(negative,[0,.1,.5,.9,1]).tolist(),
            'thresholds':[{'threshold':v, 'same_identity_pass_fraction':float(np.mean(positive>=v)),
                           'different_identity_pass_fraction':float(np.mean(negative>=v))} for v in [.55,.6,.65,.7,.75,.8]],
            'note':'Small deterministic cross-camera sample; not held-out validation.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--market-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from app.services.model_runtime import DEVICE
    from app.services.realtime import Session
    session = Session(0,'benchmark',args.reference,[0,0,1,1],args.video,False,False,lambda:None)
    session.connected = True
    frames = []
    started = time.monotonic()
    session.thread.start()
    while session.thread.is_alive():
        with session.lock:
            packet = session.packet
            if packet and packet['sequence'] > session.acked:
                frames.append({k:v for k,v in packet.items() if k != 'jpeg'})
                session.acked = packet['sequence']
                if len(frames)%20 == 0:
                    print(json.dumps({'frames':len(frames),'source_time':packet['source_time'],'state':packet['target_state']}),flush=True)
        time.sleep(.005)
    session.thread.join()
    import numpy as np
    elapsed = time.monotonic()-started
    durations = [f['processing_ms'] for f in frames]
    result = {'device':str(DEVICE),'status':session.status,'error':session.error,'wall_seconds':round(elapsed,2),
              'display_fps':round(len(frames)/max(elapsed,1e-6),2),'processed_frames':len(frames),
              'state_counts':dict(Counter(f['target_state'] for f in frames)),
              'processing_ms_p50_p95':np.quantile(durations,[.5,.95]).tolist() if durations else [],'frames':frames}
    if args.market_root:
        print('Sampling Market-1501 pairs',flush=True)
        result['market_sample'] = market_sample(args.market_root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='frames'},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
