"""Core value types shared across the system.

Nothing in this module knows about procedures, cameras or models. It is the
vocabulary both halves of the system speak, per the event contract in
docs/02-TRD.md section 4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class StepState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    SKIPPED = "skipped"
    OUT_OF_ORDER = "out_of_order"
    UNVERIFIED = "unverified"
    OVERRIDDEN = "overridden"
    STALLED = "stalled"


#: States that satisfy a precondition. A skipped step does NOT satisfy one --
#: the crew may still go back to it.
SATISFYING_STATES = frozenset({StepState.COMPLETE, StepState.OVERRIDDEN})

#: States a step will not spontaneously leave without new evidence or crew action.
RESOLVED_STATES = frozenset({StepState.COMPLETE, StepState.OVERRIDDEN, StepState.SKIPPED})


class AlertKind(StrEnum):
    SKIP = "skip"
    OUT_OF_ORDER = "out_of_order"
    STALL = "stall"
    UNVERIFIED = "unverified"
    FREE_FLOAT = "free_float"
    DEGRADED = "degraded"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventType(StrEnum):
    FRAME = "frame"
    RACK = "rack"
    DETECTION = "detection"
    HAND = "hand"
    CONTACT = "contact"
    POSE = "pose"
    STEP_STATE = "step_state"
    ALERT = "alert"
    CREW_ACTION = "crew_action"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Event:
    """One line of the event stream.

    ``t`` is capture time, not publish time -- replay depends on it being the
    moment the world was observed.
    """

    t: float
    seq: int
    src: str
    type: str
    payload: dict[str, Any]
    v: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(self.t, 4),
            "seq": self.seq,
            "src": self.src,
            "type": self.type,
            "v": self.v,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        return cls(
            t=float(d["t"]),
            seq=int(d["seq"]),
            src=str(d["src"]),
            type=str(d["type"]),
            payload=d.get("payload") or {},
            v=int(d.get("v", 1)),
        )

    @classmethod
    def from_json(cls, line: str) -> Event:
        return cls.from_dict(json.loads(line))


@dataclass(frozen=True, slots=True)
class DetectedObject:
    """One detection in one frame.

    ``centroid_mm`` is in the rack coordinate frame and is None when the rack
    is not locked -- distance predicates degrade rather than fail when it is
    missing.
    """

    cls: str
    conf: float
    bbox: tuple[float, float, float, float]
    track_id: int | None = None
    centroid_mm: tuple[float, float, float] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DetectedObject:
        centroid = d.get("centroid_mm")
        return cls(
            cls=str(d["cls"]),
            conf=float(d["conf"]),
            bbox=tuple(float(x) for x in d["bbox"]),  # type: ignore[arg-type]
            track_id=d.get("track_id"),
            centroid_mm=tuple(float(x) for x in centroid) if centroid else None,
        )

    @property
    def bbox_center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


@dataclass(frozen=True, slots=True)
class Contact:
    """A hand-object or tool-object contact observed in one frame."""

    a: str
    b_track_id: int | None
    b_cls: str | None
    conf: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Contact:
        return cls(
            a=str(d.get("a", "hand")),
            b_track_id=d.get("track_id"),
            b_cls=d.get("cls"),
            conf=float(d.get("conf", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class Marker:
    id: str
    pos_mm: tuple[float, float, float]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Marker:
        return cls(id=str(d["id"]), pos_mm=tuple(float(x) for x in d["pos_mm"]))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class RackState:
    found: bool
    rotation_deg: float
    markers: tuple[Marker, ...]
    quality: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RackState:
        return cls(
            found=bool(d.get("found", False)),
            rotation_deg=float(d.get("rotation_deg", 0.0)),
            markers=tuple(Marker.from_dict(m) for m in d.get("markers", [])),
            quality=float(d.get("quality", 0.0)),
        )
