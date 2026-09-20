"""Scenario simulator -- synthesises perception event streams without a camera.

This exists so the reasoning layer can be built, tested and demonstrated before
any model is trained (implementation plan M0/M1), and so the golden replay
corpus is reproducible rather than a pile of hand-edited JSONL.

It is not a physics simulator. It emits exactly what a working perception stack
would publish, given a world you drive by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orbital_har.core.bus import JsonlSink
from orbital_har.core.types import Event

Vec3 = tuple[float, float, float]

#: Arbitrary but fixed, so fixtures are byte-stable across runs.
DEFAULT_START_T = 1758374400.0

#: Rack-frame landmarks used by the shipped fixtures. Chosen so that stowed
#: positions fall outside the central verification region -- otherwise a dwell
#: predicate would be trivially satisfied while the object sits on the rack.
STOWAGE: Vec3 = (0.0, 400.0, 0.0)
MARKER_R: Vec3 = (250.0, -50.0, 0.0)
MARKER_Y: Vec3 = (-250.0, -50.0, 0.0)
CLIP_HOME: Vec3 = (0.0, -350.0, 0.0)
VERIFY_HOLD: Vec3 = (0.0, 0.0, 0.0)


@dataclass
class SimObject:
    obj_id: str
    cls: str
    pos: Vec3
    track_id: int
    conf: float = 0.92
    visible: bool = True


@dataclass
class Scenario:
    """Drives a world and records what perception would have said about it."""

    fps: float = 30.0
    start_t: float = DEFAULT_START_T
    width: int = 1280
    height: int = 720
    #: Pixels per millimetre for the fake projection.
    scale: float = 0.8

    objects: dict[str, SimObject] = field(default_factory=dict)
    markers: dict[str, Vec3] = field(default_factory=dict)
    contacts: list[tuple[str, str]] = field(default_factory=list)
    rack_found: bool = True
    rack_rotation_deg: float = 0.0

    _events: list[Event] = field(default_factory=list, init=False)
    _frame_id: int = field(default=0, init=False)
    _seq: int = field(default=0, init=False)

    # ------------------------------------------------------------ authoring

    def add(self, obj_id: str, cls: str, pos: Vec3, conf: float = 0.92) -> Scenario:
        self.objects[obj_id] = SimObject(
            obj_id=obj_id, cls=cls, pos=pos, track_id=len(self.objects) + 1, conf=conf
        )
        return self

    def marker(self, marker_id: str, pos: Vec3) -> Scenario:
        self.markers[marker_id] = pos
        return self

    def set_class(self, obj_id: str, cls: str) -> Scenario:
        self.objects[obj_id].cls = cls
        return self

    def place(self, obj_id: str, pos: Vec3) -> Scenario:
        self.objects[obj_id].pos = pos
        return self

    def set_conf(self, obj_id: str, conf: float) -> Scenario:
        self.objects[obj_id].conf = conf
        return self

    def set_visible(self, obj_id: str, visible: bool) -> Scenario:
        self.objects[obj_id].visible = visible
        return self

    def grab(self, actor: str, obj_id: str) -> Scenario:
        pair = (actor, obj_id)
        if pair not in self.contacts:
            self.contacts.append(pair)
        return self

    def release(self, actor: str | None = None, obj_id: str | None = None) -> Scenario:
        if actor is None and obj_id is None:
            self.contacts.clear()
        else:
            self.contacts = [
                c
                for c in self.contacts
                if not ((actor is None or c[0] == actor) and (obj_id is None or c[1] == obj_id))
            ]
        return self

    def rack_lost(self, lost: bool = True) -> Scenario:
        self.rack_found = not lost
        return self

    # ------------------------------------------------------------- emission

    def hold(self, seconds: float) -> Scenario:
        """Advance time, emitting frames at the configured rate."""
        for _ in range(max(1, round(seconds * self.fps))):
            self._emit_frame()
        return self

    @property
    def now(self) -> float:
        return self.start_t + self._frame_id / self.fps

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def _push(self, t: float, src: str, type_: str, payload: dict) -> None:
        self._seq += 1
        self._events.append(Event(t=t, seq=self._seq, src=src, type=type_, payload=payload))

    def _project(self, pos: Vec3) -> tuple[float, float, float, float]:
        cx = self.width / 2 + pos[0] * self.scale
        cy = self.height / 2 - pos[1] * self.scale
        half = 45.0
        return (cx - half, cy - half, cx + half, cy + half)

    def _emit_frame(self) -> None:
        fid = self._frame_id
        t = self.start_t + fid / self.fps
        self._frame_id += 1

        self._push(t, "capture", "frame", {"frame_id": fid, "w": self.width, "h": self.height})
        self._push(
            t,
            "rackframe",
            "rack",
            {
                "frame_id": fid,
                "found": self.rack_found,
                "rotation_deg": self.rack_rotation_deg,
                "quality": 1.0 if self.rack_found else 0.0,
                "markers": (
                    [{"id": mid, "pos_mm": list(pos)} for mid, pos in self.markers.items()]
                    if self.rack_found
                    else []
                ),
            },
        )

        objects = []
        for o in self.objects.values():
            if not o.visible:
                continue
            objects.append(
                {
                    "cls": o.cls,
                    "conf": round(o.conf, 3),
                    "bbox": [round(v, 1) for v in self._project(o.pos)],
                    "track_id": o.track_id,
                    "centroid_mm": list(o.pos) if self.rack_found else None,
                }
            )
        self._push(t, "detect", "detection", {"frame_id": fid, "objects": objects})

        if self.contacts:
            pairs = []
            for actor, obj_id in self.contacts:
                o = self.objects[obj_id]
                pairs.append(
                    {
                        "a": actor,
                        "cls": o.cls,
                        "track_id": o.track_id,
                        "conf": round(min(0.95, o.conf), 3),
                    }
                )
            self._push(t, "hands", "contact", {"frame_id": fid, "pairs": pairs})

    # ---------------------------------------------------------------- output

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        if p.exists():
            p.unlink()
        with JsonlSink(p) as sink:
            for ev in self._events:
                sink.write(ev)
        return p


def standard_world(fps: float = 30.0) -> Scenario:
    """The shared prop kit from TRD appendix A.2, stowed and closed."""
    sc = Scenario(fps=fps)
    sc.marker("R", MARKER_R).marker("Y", MARKER_Y)
    sc.add("outer_box", "outer_box_closed", STOWAGE)
    sc.add("red_box", "red_box_closed", STOWAGE)
    sc.add("yellow_box", "yellow_box_closed", STOWAGE)
    sc.add("sample_vial", "sample_vial", STOWAGE)
    sc.add("tweezers", "tweezers", (350.0, 350.0, 0.0))
    sc.add("tether_clip", "tether_clip", CLIP_HOME)
    return sc
