"""Short-gap object tracking: a detector miss is not the object leaving.

Detections arrive with no memory of the frame before. A hand passing over a
box, a moment of motion blur, or a confidence dipping under the floor makes the
object vanish for a frame -- and every hold in the engine needs its evidence on
every frame, so one miss restarts a two-second dwell from nothing. The operator
feels that as having to hold still.

The tracker gives each object a stable ``track_id`` (boxes matched by overlap
within a class) and keeps an object that goes missing for up to ``coast_s`` at
its last place, marked ``coasted``. Longer than that and it is gone: a short gap
is not evidence of absence, a long one is. The gap is measured in seconds, not
frames, so it means the same at 4 FPS as at 30.

Two things are never bridged. A different class seen in the same place is the
object changing state (cap on, cap off), not the old state hiding, so the old
one is let go at once. And one tracker serves every source -- the stock model,
a model trained on the device, colour-found blocks -- because a single frame
mixes all three; Ultralytics' own tracker follows one model's output.

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

#: Boxes of one class overlapping at least this much are the same object.
MATCH_IOU = 0.3
#: A detection overlapping a missing track this much is that spot re-seen as
#: something else: the missing one changed state, it is not hidden.
REPLACED_IOU = 0.5
#: The longest gap bridged. Long enough for a hand passing over, short enough
#: that an object really taken away is gone within half a second.
COAST_S = 0.4


def iou(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Track:
    id: int
    obj: dict[str, Any]  # the last detection payload
    seen_t: float


class ObjectTracker:
    """Stable ids for detections, and short gaps bridged."""

    def __init__(self, coast_s: float = COAST_S) -> None:
        self.coast_s = coast_s
        self._tracks: list[_Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self._tracks.clear()

    def update(self, objects: list[dict[str, Any]], t: float | None = None) -> list[dict[str, Any]]:
        """This frame's detections in, the same with ``track_id`` set, plus any
        object missing for no longer than ``coast_s``, marked ``coasted``."""
        now = time.monotonic() if t is None else t
        out: list[dict[str, Any]] = []
        matched: set[int] = set()

        # Greedy by overlap: the best pairs first, one detection per track.
        pairs = sorted(
            (
                (iou(obj["bbox"], tr.obj["bbox"]), i, tr)
                for i, obj in enumerate(objects)
                for tr in self._tracks
                if tr.obj["cls"] == obj["cls"]
            ),
            key=lambda p: p[0],
            reverse=True,
        )
        taken: dict[int, _Track] = {}
        for overlap, i, tr in pairs:
            if overlap < MATCH_IOU or i in taken or tr.id in matched:
                continue
            taken[i] = tr
            matched.add(tr.id)

        tracks: list[_Track] = []
        for i, obj in enumerate(objects):
            tr = taken.get(i)
            if tr is None:
                tr = _Track(id=self._next_id, obj=obj, seen_t=now)
                self._next_id += 1
            obj = {**obj, "track_id": tr.id}
            tr.obj, tr.seen_t = obj, now
            tracks.append(tr)
            out.append(obj)

        for tr in self._tracks:
            if tr.id in matched or now - tr.seen_t > self.coast_s:
                continue
            if any(iou(o["bbox"], tr.obj["bbox"]) >= REPLACED_IOU for o in objects):
                continue  # something else is seen there now: it changed, it is not hidden
            tracks.append(tr)
            out.append({**tr.obj, "coasted": True})

        self._tracks = tracks
        return out
