"""Rolling observation window.

Perception events arrive one at a time and are grouped into per-frame
snapshots. Predicates read the window; they never read the bus directly. This
is what keeps predicate evaluation a pure function of recent history and so
unit-testable with no camera and no models present.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from orbital_har.core.types import Contact, DetectedObject, Event, EventType, RackState

DEFAULT_CAPACITY = 240  # ~8 s at 30 FPS


@dataclass
class FrameSnapshot:
    """Everything perception said about one frame."""

    frame_id: int
    t: float
    width: int = 1280
    height: int = 720
    objects: list[DetectedObject] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    rack: RackState | None = None

    def best(self, classes: set[str], min_conf: float = 0.0) -> DetectedObject | None:
        """Highest-confidence detection whose class is in ``classes``."""
        candidates = [o for o in self.objects if o.cls in classes and o.conf >= min_conf]
        if not candidates:
            return None
        return max(candidates, key=lambda o: o.conf)

    def count(self, class_name: str, min_conf: float) -> int:
        return sum(1 for o in self.objects if o.cls == class_name and o.conf >= min_conf)

    def track(self, track_id: int) -> DetectedObject | None:
        for o in self.objects:
            if o.track_id == track_id:
                return o
        return None

    def marker_pos(self, marker_id: str) -> tuple[float, float, float] | None:
        if self.rack is None or not self.rack.found:
            return None
        for m in self.rack.markers:
            if m.id == marker_id:
                return m.pos_mm
        return None


class Window:
    """Bounded history of frame snapshots, oldest first."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._frames: deque[FrameSnapshot] = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._frames)

    def append(self, snapshot: FrameSnapshot) -> None:
        self._frames.append(snapshot)

    @property
    def latest(self) -> FrameSnapshot | None:
        return self._frames[-1] if self._frames else None

    def last(self, n: int) -> list[FrameSnapshot]:
        """The most recent ``n`` snapshots, oldest first.

        Returns fewer than ``n`` when history is short -- callers requiring a
        full streak must check the length themselves.
        """
        if n <= 0:
            return []
        return list(self._frames)[-n:]

    def since(self, t: float) -> list[FrameSnapshot]:
        return [f for f in self._frames if f.t >= t]

    def clear(self) -> None:
        self._frames.clear()


class SnapshotAssembler:
    """Groups the per-frame event fan-out back into single snapshots.

    Perception publishes several events per frame, all carrying ``frame_id``.
    A snapshot is finalized when a new frame_id appears, so the assembler is
    always exactly one frame behind -- about 33 ms, which is well inside the
    latency budget and buys ordering independence between producers.
    """

    def __init__(self) -> None:
        self._current: FrameSnapshot | None = None

    def feed(self, event: Event) -> FrameSnapshot | None:
        """Consume an event; return a finished snapshot when one closes."""
        payload = event.payload
        frame_id = payload.get("frame_id")

        if event.type == EventType.FRAME.value:
            frame_id = payload.get("frame_id", frame_id)

        if frame_id is None:
            return None

        finished: FrameSnapshot | None = None
        if self._current is not None and self._current.frame_id != frame_id:
            finished = self._current
            self._current = None

        if self._current is None:
            self._current = FrameSnapshot(frame_id=int(frame_id), t=event.t)

        snap = self._current

        if event.type == EventType.FRAME.value:
            snap.width = int(payload.get("w", snap.width))
            snap.height = int(payload.get("h", snap.height))
            snap.t = event.t
        elif event.type == EventType.DETECTION.value:
            snap.objects.extend(DetectedObject.from_dict(o) for o in payload.get("objects", []))
        elif event.type == EventType.CONTACT.value:
            snap.contacts.extend(Contact.from_dict(c) for c in payload.get("pairs", []))
        elif event.type == EventType.RACK.value:
            snap.rack = RackState.from_dict(payload)

        return finished

    def flush(self) -> FrameSnapshot | None:
        """Close the in-progress snapshot at end of stream."""
        finished, self._current = self._current, None
        return finished
