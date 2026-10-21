"""Session-local ByteTrack adapter; model weights remain shared."""
from types import SimpleNamespace

import numpy as np


def create_tracker(fps=10):
    # Check explicitly before Ultralytics can attempt an automatic installation.
    import lap  # noqa: F401
    from ultralytics.trackers.byte_tracker import BYTETracker
    class IndexedTracker(BYTETracker):
        def init_track(self, results, img=None):
            tracks = super().init_track(results, img)
            # Ultralytics 8.4.60 renumbers each high/low-score subset. Preserve
            # the original detector index across both association passes.
            for track, index in zip(tracks, results.indices):
                track.idx = int(index)
            return tracks

    # In 8.4.60 score fusion also applies to the second pass; with a 0.5
    # cost threshold it rejects every low-score detection. Use IoU association.
    return IndexedTracker(SimpleNamespace(track_high_thresh=0.25, track_low_thresh=0.1,
        new_track_thresh=0.25, track_buffer=round(fps * 3), match_thresh=0.8, fuse_score=False))


class DetectionBatch:
    def __init__(self, data, indices=None):
        self.data = data
        self.indices = np.arange(len(data)) if indices is None else indices
        self.conf = data[:, 4]
        self.cls = data[:, 5]
        self.xyxy = data[:, :4]
        self.xywh = self.xyxy.copy()
        self.xywh[:, :2] = (self.xyxy[:, :2] + self.xyxy[:, 2:]) / 2
        self.xywh[:, 2:] = self.xyxy[:, 2:] - self.xyxy[:, :2]

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return DetectionBatch(self.data[index], self.indices[index])


def update_tracker(tracker, detections, shape):
    data = np.asarray([[d.x1, d.y1, d.x2, d.y2, d.confidence, 0] for d in detections],
                      dtype=np.float32).reshape(-1, 6)
    return tracker.update(DetectionBatch(data))


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / max(1, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection)


class IdentityGate:
    """Only fresh ReID measurements advance confirmation; absence clears identity."""
    def __init__(self, enter=0.65, leave=0.55, margin=0.08, confirmations=3):
        self.enter, self.leave, self.margin, self.confirmations = enter, leave, margin, confirmations
        self.target = self.candidate = None
        self.hits = 0
        self.ever_confirmed = False

    def update(self, scores, *, measured=True, crowded=False):
        previous = self.target
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        threshold = self.leave if ranked and ranked[0][0] == previous else self.enter
        valid = bool(ranked and ranked[0][1] >= threshold and not crowded and
                     (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= self.margin))
        if not valid:
            self.target = self.candidate = None
            self.hits = 0
            return ("pending" if ranked and ranked[0][1] >= self.leave else
                    "lost" if self.ever_confirmed else "searching"), None
        best = ranked[0][0]
        if previous == best:
            return "confirmed", best
        self.target = None
        if best != self.candidate:
            self.candidate, self.hits = best, 0
        if measured:
            self.hits += 1
        if self.hits >= self.confirmations:
            self.target = best
            state = "reconfirmed" if self.ever_confirmed else "confirmed"
            self.ever_confirmed = True
            return state, best
        return "pending", None
