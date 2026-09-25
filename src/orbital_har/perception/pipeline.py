"""The perception pipeline: one camera frame in, observations out.

This is the producing half of the event-bus seam. It localises the rack,
canonicalizes the input frame, detects objects, estimates pose, derives hands
and infers contact — and emits all of it as event payloads. It has no idea what
a procedure is, which step is active, or what any of this will be judged
against. That ignorance is the point: it is what lets the reasoning layer be
built and regression-tested with no camera present.

Nothing here may import from ``orbital_har.reasoning``. The import-linter
contract in pyproject.toml enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from orbital_har.core.types import EventType
from orbital_har.perception.detect import Detector
from orbital_har.perception.hands import ContactInferrer, HandPoint, HandTracker
from orbital_har.perception.pose import PoseEstimator, PoseObservation
from orbital_har.perception.rackframe import RackFrame, RackLayout, RackObservation

#: (source, event type, payload) — everything observed about one frame.
Emission = tuple[str, str, dict[str, Any]]


@dataclass
class Observation:
    """One frame's worth of perception output."""

    frame: np.ndarray
    emissions: list[Emission] = field(default_factory=list)
    rack: RackObservation | None = None
    pose: PoseObservation | None = None
    hands: tuple[HandPoint, ...] = ()
    objects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rack_locked(self) -> bool:
        return self.rack is not None and self.rack.found


class PerceptionPipeline:
    """Runs detection, and optionally rack localisation and pose, per frame.

    Rack and pose are switched on per procedure by the caller rather than
    always: a procedure that only says "detect the bottle" should not pay for a
    pose inference on every frame.
    """

    def __init__(self, detector: Detector) -> None:
        self.detector = detector
        self.rack: RackFrame | None = None
        self.pose: PoseEstimator | None = None
        self.want_pose = False
        self.hands = HandTracker()
        self.contacts = ContactInferrer()
        self.rack_locked = False

    # ------------------------------------------------------------- configure

    def configure(
        self, *, want_rack: bool, want_pose: bool, rack_dictionary: str = "DICT_4X4_50"
    ) -> None:
        """Match the stack to what the loaded procedure needs.

        The pose model is loaded once and kept even when a later procedure stops
        needing it: unloading only to reload on the next hot-swap would stall a
        live run for seconds.
        """
        self.rack = RackFrame(RackLayout(dictionary=rack_dictionary)) if want_rack else None
        self.rack_locked = False
        self.want_pose = want_pose
        if want_pose and self.pose is None:
            self.pose = PoseEstimator()
            if not self.pose.load():
                print(f"[perception] pose unavailable: {self.pose.unavailable_reason}")

    def status(self) -> dict[str, Any]:
        """What the UI needs to show a degraded run honestly (invariant #10)."""
        return {
            "rack_required": self.rack is not None,
            "rack_locked": self.rack_locked,
            "pose_required": self.want_pose,
            "pose_ok": self.pose is not None and self.pose.available,
            "pose_error": self.pose.unavailable_reason if self.pose else None,
            "detector": self.detector.mode,
        }

    # --------------------------------------------------------------- observe

    def observe(
        self,
        frame: np.ndarray,
        frame_id: int,
        *,
        wanted: set[str],
        min_area: float = 0.06,
    ) -> Observation:
        """Perceive one frame. Returns the annotated frame and its emissions."""
        rack_obs: RackObservation | None = None

        # Rack first. Canonicalize the *input* frame by the rack's tilt so the
        # detector always sees an upright scene (invariant #4), then rebase the
        # homography into the rotated frame's coordinates so detections and
        # positions share one space.
        if self.rack is not None:
            rack_obs = self.rack.observe(frame)
            frame, _, inverse = RackFrame.canonicalize(frame, rack_obs.rotation_deg)
            rack_obs = rack_obs.rebased(inverse)
            self.rack_locked = rack_obs.found
            RackFrame.draw(frame, rack_obs)

        detection = self.detector.detect(
            frame, wanted, min_area=min_area, multi=self.rack is not None
        )
        Detector.draw(frame, detection)
        objects = detection.objects

        # Rack-frame positions. Without a lock these stay absent and every
        # distance predicate degrades to "cannot tell" instead of guessing.
        if rack_obs is not None and rack_obs.found:
            for obj in objects:
                x0, y0, x1, y1 = obj["bbox"]
                pos = rack_obs.project(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
                if pos is not None:
                    obj["centroid_mm"] = [round(v, 1) for v in pos]

        pose_obs: PoseObservation | None = None
        hands: tuple[HandPoint, ...] = ()
        pairs: list[dict[str, Any]] = []
        if self.want_pose and self.pose is not None:
            pose_obs = self.pose.observe(frame)
            hands = self.hands.observe(pose_obs)
            pairs = self.contacts.infer(hands, objects)
            for hand in hands:
                cv2.circle(frame, (int(hand.x), int(hand.y)), 9, (255, 190, 0), 2)

        h, w = frame.shape[:2]
        emissions: list[Emission] = [
            ("capture", EventType.FRAME.value, {"frame_id": frame_id, "w": w, "h": h})
        ]
        if rack_obs is not None:
            emissions.append(("rackframe", EventType.RACK.value, rack_obs.to_payload(frame_id)))
        emissions.append(
            ("detect", EventType.DETECTION.value, {"frame_id": frame_id, "objects": objects})
        )
        if hands:
            emissions.append(
                ("hands", EventType.HAND.value, HandTracker.to_payload(frame_id, hands))
            )
        if pairs:
            emissions.append(
                ("hands", EventType.CONTACT.value, ContactInferrer.to_payload(frame_id, pairs))
            )
        if pose_obs is not None and pose_obs.present:
            emissions.append(("pose", EventType.POSE.value, pose_obs.to_payload(frame_id)))

        return Observation(
            frame=frame,
            emissions=emissions,
            rack=rack_obs,
            pose=pose_obs,
            hands=hands,
            objects=objects,
        )
