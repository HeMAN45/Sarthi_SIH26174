"""The perception pipeline: one camera frame in, observations out.

This is the producing half of the event-bus seam. It localises the rack,
canonicalizes the input frame, detects objects, estimates pose, derives hands
and infers contact - and emits all of it as event payloads. It has no idea what
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

from orbital_har.core.types import EventType, Gesture
from orbital_har.perception.detect import Detector
from orbital_har.perception.gestures import GestureReader
from orbital_har.perception.hands import ContactInferrer, HandPoint, HandTracker
from orbital_har.perception.pose import PoseEstimator, PoseObservation
from orbital_har.perception.rackframe import RackFrame, RackLayout, RackObservation
from orbital_har.perception.tracking import ObjectTracker

#: (source, event type, payload) - everything observed about one frame.
Emission = tuple[str, str, dict[str, Any]]

#: Input size for the stock detector and the pose model. Measured on 150 frames
#: of recorded runs against 640 (the models' own size), on an i5-1135G7 CPU:
#: 416 keeps 94% of the objects, finds the person in every frame with 1.7%
#: keypoint drift, and runs detection 67 -> 37 ms and pose 74 -> 40 ms. 320 was
#: barely faster and lost more. Raise it when the objects are small in frame.
DEFAULT_IMGSZ = 416


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


#: COCO-17 limbs, for the skeleton overlay.
_LIMBS = (
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 6),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (0, 1),
    (0, 2),
    (1, 3),
    (2, 4),
)
_BONE = (235, 200, 90)  # BGR: pale cyan skeleton
_JOINT = (255, 255, 255)
_PALM = (46, 154, 243)  # BGR: saffron, the console accent
_TOUCH = (90, 210, 110)
_GESTURE_LABEL = {
    "hand_raised": "HAND RAISED",
    "hand_to_face": "HAND TO FACE",
    "hand_on_head": "HAND ON HEAD",
    "reaching": "REACHING",
    "waving": "WAVING",
    "lifting": "LIFTING",
    "lowering": "LOWERING",
    "both_hands_raised": "BOTH HANDS UP",
    "hands_together": "HANDS TOGETHER",
    "arms_crossed": "ARMS CROSSED",
    "arms_out": "ARMS OUT",
    "hands_on_hips": "HANDS ON HIPS",
    "clapping": "CLAPPING",
}


class PerceptionPipeline:
    """Runs detection, rack localisation and body tracking, per frame.

    Rack localisation is switched on per procedure: it needs markers in view.
    Body tracking -- pose, hands, hand-object contact and gestures -- runs by
    default, because watching the astronaut is the point of the system; it is
    forced on for procedures whose steps need it and can be switched off to
    save CPU on a weak machine when none do.
    """

    def __init__(self, detector: Detector, imgsz: int | None = DEFAULT_IMGSZ) -> None:
        self.detector = detector
        #: Input size for the stock detector and pose; None keeps their own.
        self.imgsz = imgsz
        self.rack: RackFrame | None = None
        self.pose: PoseEstimator | None = None
        self.want_pose = False
        #: Report every object in view, not only what is held up close.
        self.scene = False
        self.body = True
        self.hands = HandTracker()
        self.contacts = ContactInferrer()
        self.gestures = GestureReader()
        #: Stable ids, and a detector miss of a moment bridged (``tracking``).
        self.tracker = ObjectTracker()
        self.rack_locked = False
        #: Image-space regions of the loaded procedure, drawn so the operator
        #: can see where "put it back in its place" means. Display only.
        self.regions: list[tuple[str, tuple[float, float, float, float]]] = []
        self._body_state: dict[str, Any] = {"tracked": False, "gestures": [], "contacts": []}

    # ------------------------------------------------------------- configure

    def configure(
        self,
        *,
        want_rack: bool,
        want_pose: bool,
        rack_dictionary: str = "DICT_4X4_50",
        regions: list[tuple[str, tuple[float, float, float, float]]] | None = None,
        scene: bool = False,
    ) -> None:
        """Match the stack to what the loaded procedure needs.

        ``scene`` is for procedures that relate objects to hands, places or
        each other: every object in view is reported. Otherwise only objects
        held up to the camera are -- a presentation.

        The pose model is loaded once and kept even when a later procedure stops
        needing it: unloading only to reload on the next hot-swap would stall a
        live run for seconds.
        """
        self.rack = RackFrame(RackLayout(dictionary=rack_dictionary)) if want_rack else None
        self.rack_locked = False
        self.want_pose = want_pose
        self.scene = scene
        self.regions = list(regions or [])
        self.tracker.reset()
        if self.tracking_body:
            self._ensure_pose()

    def set_body(self, on: bool) -> None:
        """Body tracking on or off. A procedure that needs it keeps it on."""
        self.body = bool(on)
        if self.tracking_body:
            self._ensure_pose()

    @property
    def tracking_body(self) -> bool:
        return self.want_pose or self.body

    def _ensure_pose(self) -> None:
        if self.pose is None:
            self.pose = PoseEstimator(imgsz=self.imgsz)
            if not self.pose.load():
                print(f"[perception] pose unavailable: {self.pose.unavailable_reason}")

    def status(self) -> dict[str, Any]:
        """What the UI needs to show a degraded run honestly (invariant #10)."""
        pose_ok = self.pose is not None and self.pose.available
        return {
            "rack_required": self.rack is not None,
            "rack_locked": self.rack_locked,
            "pose_required": self.want_pose,
            "pose_ok": pose_ok,
            "pose_error": self.pose.unavailable_reason if self.pose else None,
            "detector": self.detector.mode,
            "trained": list(getattr(self.detector, "trained_classes", [])),
            "body": {
                "enabled": self.tracking_body,
                "forced": self.want_pose,
                "ready": pose_ok,
                **self._body_state,
            },
        }

    # --------------------------------------------------------------- observe

    def observe(
        self,
        frame: np.ndarray,
        frame_id: int,
        *,
        wanted: set[str],
        min_area: float = 0.06,
        mirror: bool = False,
    ) -> Observation:
        """Perceive one frame. Returns an annotated copy and the emissions.

        The input frame is never written to. Every model sees the clean image,
        and so does anything else holding the same buffer -- training capture
        included, which used to save the overlay into its training photos.

        ``mirror`` flips only the returned picture, for a selfie-style view.
        Perception, and every coordinate it emits, stays in the camera's true
        orientation: ArUco markers do not decode mirrored, and a payload that
        moved with a display preference would not be evidence.
        """
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

        detection = self.detector.detect(
            frame,
            wanted,
            min_area=min_area,
            multi=self.scene or self.rack is not None,
            imgsz=self.imgsz,
        )
        objects = detection.objects

        # Rack-frame positions. Without a lock these stay absent and every
        # distance predicate degrades to "cannot tell" instead of guessing.
        if rack_obs is not None and rack_obs.found:
            for obj in objects:
                x0, y0, x1, y1 = obj["bbox"]
                pos = rack_obs.project(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
                if pos is not None:
                    obj["centroid_mm"] = [round(v, 1) for v in pos]

        # A whole-frame classifier has no boxes to follow; everything else does.
        if self.detector.mode != "classifier":
            objects = self.tracker.update(objects)

        pose_obs: PoseObservation | None = None
        hands: tuple[HandPoint, ...] = ()
        pairs: list[dict[str, Any]] = []
        gestures: tuple[Gesture, ...] = ()
        if self.tracking_body and self.pose is not None:
            pose_obs = self.pose.observe(frame)
            hands = self.hands.observe(pose_obs)
            # A whole-frame classifier reports the entire image as the
            # object's box, so every hand would "touch" it. Contact needs real
            # boxes; without them it is not measured rather than invented.
            if self.detector.mode != "classifier":
                pairs = self.contacts.infer(hands, objects)
            gestures = self.gestures.read(pose_obs.primary)
        self._body_state = {
            "tracked": bool(pose_obs and pose_obs.present),
            "gestures": [{"name": g.name, "side": g.side, "conf": g.conf} for g in gestures],
            "contacts": [
                {"side": c.get("side"), "object": c.get("cls")}
                for c in pairs
                if c.get("a") == "hand"
            ],
        }

        h, w = frame.shape[:2]

        # Overlay last, on a copy: nothing above ever saw an annotation.
        canvas = cv2.flip(frame, 1) if mirror else frame.copy()
        self._draw_regions(canvas, mirror)
        if rack_obs is not None:
            RackFrame.draw(canvas, rack_obs)
        Detector.draw(canvas, detection, mirror=mirror)
        self._draw_body(canvas, pose_obs, hands, pairs, objects, gestures, mirror)

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
        if gestures:
            emissions.append(
                ("gestures", EventType.GESTURE.value, GestureReader.to_payload(frame_id, gestures))
            )

        return Observation(
            frame=canvas,
            emissions=emissions,
            rack=rack_obs,
            pose=pose_obs,
            hands=hands,
            objects=objects,
        )

    # --------------------------------------------------------------- overlay

    def _draw_regions(self, canvas: np.ndarray, mirror: bool) -> None:
        h, w = canvas.shape[:2]
        for name, (x0, y0, x1, y1) in self.regions:
            a, b = int(x0 * w), int(x1 * w)
            if mirror:
                a, b = w - 1 - b, w - 1 - a
            cv2.rectangle(canvas, (a, int(y0 * h)), (b, int(y1 * h)), _PALM, 2)
            cv2.putText(
                canvas,
                name.upper(),
                (a + 8, int(y0 * h) + 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                _PALM,
                2,
                cv2.LINE_AA,
            )

    def _draw_body(
        self,
        canvas: np.ndarray,
        pose_obs: PoseObservation | None,
        hands: tuple[HandPoint, ...],
        pairs: list[dict[str, Any]],
        objects: list[dict[str, Any]],
        gestures: tuple[Gesture, ...],
        mirror: bool,
    ) -> None:
        """Skeleton, palms, what each hand touches, and the gestures read."""
        w = canvas.shape[1]

        def at(x: float, y: float) -> tuple[int, int]:
            return (int(w - 1 - x) if mirror else int(x), int(y))

        person = pose_obs.primary if pose_obs is not None else None
        if person is not None:
            for a, b in _LIMBS:
                ka, kb = person.get(a, 0.35), person.get(b, 0.35)
                if ka is not None and kb is not None:
                    cv2.line(canvas, at(ka.x, ka.y), at(kb.x, kb.y), _BONE, 2, cv2.LINE_AA)
            for i in range(len(person.keypoints)):
                k = person.get(i, 0.35)
                if k is not None:
                    cv2.circle(canvas, at(k.x, k.y), 3, _JOINT, -1, cv2.LINE_AA)

        by_side = {hand.side: hand for hand in hands}
        for hand in hands:
            cv2.circle(canvas, at(hand.x, hand.y), 11, _PALM, 2, cv2.LINE_AA)
        for pair in pairs:
            hand = by_side.get(pair.get("side", ""))
            target = next((o for o in objects if o.get("cls") == pair.get("cls")), None)
            if hand is None or target is None:
                continue
            x0, y0, x1, y1 = target["bbox"]
            cv2.line(canvas, at(hand.x, hand.y), at((x0 + x1) / 2, (y0 + y1) / 2), _TOUCH, 2)

        if gestures and person is not None:
            anchor = person.get(0, 0.35) or person.get(5, 0.35) or person.get(6, 0.35)
            if anchor is None:
                return
            x, y = at(anchor.x, anchor.y)
            labels = [
                _GESTURE_LABEL.get(g.name, g.name)
                + ("" if g.side == "both" else f" ({g.side[0].upper()})")
                for g in gestures
            ]
            for i, text in enumerate(labels):
                ty = max(20, y - 40 - 24 * (len(labels) - 1 - i))
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                tx = max(4, min(w - tw - 8, x - tw // 2))
                box = ((tx - 5, ty - th - 6), (tx + tw + 5, ty + 5))
                cv2.rectangle(canvas, box[0], box[1], (20, 20, 20), -1)
                cv2.putText(
                    canvas, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.55, _PALM, 2, cv2.LINE_AA
                )
