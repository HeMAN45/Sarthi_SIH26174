"""Body pose estimation.

YOLO11-pose over MediaPipe Pose, per TRD section 6: one runtime, one export
path, and it survives the partial views a fixed payload camera actually gets --
torso and arms, rarely legs.

Seventeen COCO keypoints. The engine never reads raw keypoints; what matters
downstream is where the hands are, which ``perception.hands`` derives from here.
Pose is published anyway because the PS asks for it and because "the operator is
not in frame" is a degradation the UI must be able to show.

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

#: COCO-17 indices we actually use by name.
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14

KEYPOINT_NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


@dataclass(frozen=True, slots=True)
class Keypoint:
    x: float
    y: float
    conf: float

    @property
    def xy(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class PersonPose:
    """One detected operator in one frame."""

    keypoints: tuple[Keypoint, ...]
    conf: float

    def get(self, index: int, min_conf: float = 0.3) -> Keypoint | None:
        if index >= len(self.keypoints):
            return None
        kp = self.keypoints[index]
        return kp if kp.conf >= min_conf else None

    @property
    def torso_angle_deg(self) -> float | None:
        """Shoulder-line angle. The operator's own "up", such as it is.

        Reported for the UI and for telemetry, never used to rotate anything:
        we canonicalize the input frame by the *rack*, not by the body.
        """
        left, right = self.get(L_SHOULDER), self.get(R_SHOULDER)
        if left is None or right is None:
            return None
        import math

        return math.degrees(math.atan2(right.y - left.y, right.x - left.x))


@dataclass(frozen=True)
class PoseObservation:
    people: tuple[PersonPose, ...] = ()

    @property
    def present(self) -> bool:
        return bool(self.people)

    @property
    def primary(self) -> PersonPose | None:
        """The operator: the highest-confidence person in frame."""
        return max(self.people, key=lambda p: p.conf) if self.people else None

    def to_payload(self, frame_id: int) -> dict[str, Any]:
        return {
            "frame_id": frame_id,
            "present": self.present,
            "people": [
                {
                    "conf": round(p.conf, 3),
                    "torso_deg": (
                        None if p.torso_angle_deg is None else round(p.torso_angle_deg, 1)
                    ),
                    "keypoints": [
                        [round(k.x, 1), round(k.y, 1), round(k.conf, 3)] for k in p.keypoints
                    ],
                }
                for p in self.people
            ],
        }


class PoseEstimator:
    """Thin wrapper over an Ultralytics pose model.

    The model is loaded lazily and a failure to load is survivable: pose is
    enrichment, and losing it must not stop supervision (invariant #9). When it
    is unavailable ``unavailable_reason`` is set so the UI can say so out loud
    rather than quietly showing nothing (invariant #10).
    """

    def __init__(self, weights: str = "yolo11n-pose.pt", min_conf: float = 0.35) -> None:
        self.weights = weights
        self.min_conf = min_conf
        self.unavailable_reason: str | None = None
        self._model: Any = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        if self._model is not None:
            return True
        try:
            from ultralytics import YOLO

            self._model = YOLO(self.weights)
            self.unavailable_reason = None
            return True
        except Exception as exc:
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            self._model = None
            return False

    def observe(self, frame: np.ndarray) -> PoseObservation:
        if self._model is None:
            return PoseObservation()
        try:
            result = self._model.predict(frame, conf=self.min_conf, verbose=False)[0]
        except Exception as exc:
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            return PoseObservation()

        kp = getattr(result, "keypoints", None)
        if kp is None or kp.data is None or len(kp.data) == 0:
            return PoseObservation()

        confs = (
            result.boxes.conf.tolist()
            if getattr(result, "boxes", None) is not None and result.boxes is not None
            else []
        )
        people: list[PersonPose] = []
        for i, person in enumerate(kp.data.tolist()):
            points = tuple(
                Keypoint(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 1.0)
                for p in person
            )
            people.append(
                PersonPose(keypoints=points, conf=float(confs[i]) if i < len(confs) else 1.0)
            )
        return PoseObservation(people=tuple(people))
