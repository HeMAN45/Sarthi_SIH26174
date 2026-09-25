"""Hand localisation and hand-object contact inference.

Contact is **inferred, not learned** (docs/architecture.md). Training a contact
classifier would need a labelled contact dataset we do not have and cannot
cheaply build; geometry gets us most of the way for a fraction of the cost, and
it degrades in ways we can explain to a jury.

Hand points come from the pose model's wrists, pushed forward along the forearm
so they land on the palm rather than the joint. That is a better contact anchor
than the raw wrist and costs one subtraction.

Two kinds of contact are emitted:

* **hand -> object** -- a hand point inside or just outside an object's box.
* **object -> object** -- two boxes touching, which is how "transfer the vial
  with the tweezers" is observed without a tool-specific model.

Confidence falls off with distance rather than switching on and off, because the
engine's whole abstention design assumes graded evidence. A binary contact flag
would make every marginal grasp either a false positive or invisible.

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from orbital_har.perception.pose import (
    L_ELBOW,
    L_WRIST,
    R_ELBOW,
    R_WRIST,
    PoseObservation,
)

#: How far past the wrist, as a fraction of the forearm, the palm sits.
_PALM_EXTEND = 0.28

#: Contact margin as a fraction of the object's larger side, clamped in pixels.
_MARGIN_RATIO = 0.22
_MARGIN_MIN_PX = 18.0
_MARGIN_MAX_PX = 110.0


@dataclass(frozen=True, slots=True)
class HandPoint:
    side: str  # "left" | "right"
    x: float
    y: float
    conf: float

    @property
    def xy(self) -> tuple[float, float]:
        return (self.x, self.y)


def _margin_for(bbox: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = bbox
    span = max(x1 - x0, y1 - y0)
    return min(_MARGIN_MAX_PX, max(_MARGIN_MIN_PX, span * _MARGIN_RATIO))


def _point_box_distance(
    point: tuple[float, float], bbox: tuple[float, float, float, float]
) -> float:
    """Euclidean distance from a point to a box. Zero when inside."""
    px, py = point
    x0, y0, x1, y1 = bbox
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    return math.hypot(dx, dy)


def _box_gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Shortest gap between two boxes. Zero when they overlap or touch."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(bx0 - ax1, 0.0, ax0 - bx1)
    dy = max(by0 - ay1, 0.0, ay0 - by1)
    return math.hypot(dx, dy)


def _falloff(distance: float, margin: float) -> float:
    """1.0 on touch, tapering to 0.0 at the margin."""
    if margin <= 0.0:
        return 1.0 if distance <= 0.0 else 0.0
    return max(0.0, 1.0 - distance / margin)


class HandTracker:
    """Derives hand points from body pose.

    A dedicated hand model (MediaPipe) would give finger-level landmarks, but
    the fixed payload camera rarely resolves fingers anyway, and the TRD already
    committed to one pose runtime. Palm-from-forearm is the honest middle.
    """

    def __init__(self, min_conf: float = 0.3) -> None:
        self.min_conf = min_conf

    def observe(self, pose: PoseObservation) -> tuple[HandPoint, ...]:
        hands: list[HandPoint] = []
        for person in pose.people:
            for side, wrist_idx, elbow_idx in (
                ("left", L_WRIST, L_ELBOW),
                ("right", R_WRIST, R_ELBOW),
            ):
                wrist = person.get(wrist_idx, self.min_conf)
                if wrist is None:
                    continue
                elbow = person.get(elbow_idx, self.min_conf)
                x, y = wrist.x, wrist.y
                if elbow is not None:
                    # Push past the wrist along the forearm onto the palm.
                    x += (wrist.x - elbow.x) * _PALM_EXTEND
                    y += (wrist.y - elbow.y) * _PALM_EXTEND
                hands.append(HandPoint(side=side, x=x, y=y, conf=wrist.conf))
        return tuple(hands)

    @staticmethod
    def to_payload(frame_id: int, hands: tuple[HandPoint, ...]) -> dict[str, Any]:
        return {
            "frame_id": frame_id,
            "hands": [
                {"side": h.side, "xy": [round(h.x, 1), round(h.y, 1)], "conf": round(h.conf, 3)}
                for h in hands
            ],
        }


class ContactInferrer:
    """Turns geometry into ``contact`` event pairs."""

    def __init__(self, min_conf: float = 0.25) -> None:
        #: Floor for emitting a pair at all. The engine still applies its own
        #: thresholds; this only keeps the event stream from filling with noise.
        self.min_conf = min_conf

    def infer(
        self, hands: tuple[HandPoint, ...], objects: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """``objects`` are detection payload dicts: cls, conf, bbox, track_id."""
        pairs: list[dict[str, Any]] = []
        boxed = [o for o in objects if o.get("bbox")]

        for hand in hands:
            for obj in boxed:
                bbox = tuple(float(v) for v in obj["bbox"])
                distance = _point_box_distance(hand.xy, bbox)  # type: ignore[arg-type]
                conf = _falloff(distance, _margin_for(bbox)) * hand.conf  # type: ignore[arg-type]
                if conf >= self.min_conf:
                    pairs.append(
                        {
                            "a": "hand",
                            "cls": obj["cls"],
                            "track_id": obj.get("track_id"),
                            "conf": round(conf, 3),
                            "side": hand.side,
                        }
                    )

        # Tool-object contact. Emitted in both directions so a procedure may
        # write the pair in whichever order reads naturally.
        for i, first in enumerate(boxed):
            for second in boxed[i + 1 :]:
                if first["cls"] == second["cls"]:
                    continue
                box_a = tuple(float(v) for v in first["bbox"])
                box_b = tuple(float(v) for v in second["bbox"])
                gap = _box_gap(box_a, box_b)  # type: ignore[arg-type]
                margin = min(_margin_for(box_a), _margin_for(box_b))  # type: ignore[arg-type]
                conf = _falloff(gap, margin) * min(
                    float(first.get("conf", 1.0)), float(second.get("conf", 1.0))
                )
                if conf < self.min_conf:
                    continue
                pairs.append(
                    {
                        "a": first["cls"],
                        "cls": second["cls"],
                        "track_id": second.get("track_id"),
                        "conf": round(conf, 3),
                    }
                )
                pairs.append(
                    {
                        "a": second["cls"],
                        "cls": first["cls"],
                        "track_id": first.get("track_id"),
                        "conf": round(conf, 3),
                    }
                )
        return pairs

    @staticmethod
    def to_payload(frame_id: int, pairs: list[dict[str, Any]]) -> dict[str, Any]:
        return {"frame_id": frame_id, "pairs": pairs}
