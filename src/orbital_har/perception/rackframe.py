"""Rack fiducial detection and input-frame canonicalization.

The payload rack, not the floor, is this system's frame of reference. An
astronaut has no fixed "up", so every spatial claim the engine makes -- "the red
box is near marker R" -- is expressed in rack millimetres, never image pixels.

Two jobs live here:

*Localisation.* Four corner fiducials define a plane. A homography maps image
pixels onto that plane in millimetres, so a bounding-box centroid becomes a
rack-frame coordinate. Named fiducials (R, Y, ...) become placement targets the
procedure can refer to by name.

*Canonicalization.* We rotate the **input frame** so the rack is upright before
anything looks at it -- never the model's output. Rotating output does not work:
an inverted operator was never detected in the first place, so there is no
output to rotate (INVARIANTS.md invariant #4, TRD section 6.1).

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np

#: Procedure files name their dictionary as a string; map it to the cv2 constant.
_DICTS = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}


@dataclass(frozen=True)
class RackLayout:
    """Physical description of the rack board.

    ``corner_ids`` are top-left, top-right, bottom-right, bottom-left in the
    rack's own orientation -- print them on the board in that order and the
    homography comes out right regardless of how the camera is held.

    ``named`` maps a fiducial id to the marker name a procedure refers to
    (``markers: [R, Y]`` in the YAML). Those are placement targets, not
    structure, so a missing one degrades that predicate and nothing else.
    """

    corner_ids: tuple[int, int, int, int] = (0, 1, 2, 3)
    width_mm: float = 600.0
    height_mm: float = 400.0
    named: dict[int, str] = field(default_factory=lambda: {10: "R", 11: "Y"})
    dictionary: str = "DICT_4X4_50"

    def corner_targets(self) -> np.ndarray:
        """Corner positions in rack millimetres, matching ``corner_ids`` order."""
        return np.array(
            [
                [0.0, 0.0],
                [self.width_mm, 0.0],
                [self.width_mm, self.height_mm],
                [0.0, self.height_mm],
            ],
            dtype=np.float32,
        )


@dataclass(frozen=True)
class RackObservation:
    """What the rack looked like in one frame."""

    found: bool
    rotation_deg: float
    quality: float
    markers: tuple[tuple[str, tuple[float, float, float]], ...] = ()
    #: Image -> rack-plane homography. None whenever the rack is not locked.
    homography: np.ndarray | None = None
    corners_seen: int = 0

    def to_payload(self, frame_id: int) -> dict[str, Any]:
        """Payload for a ``rack`` event (parsed by core.types.RackState)."""
        return {
            "frame_id": frame_id,
            "found": self.found,
            "rotation_deg": round(self.rotation_deg, 2),
            "quality": round(self.quality, 3),
            "markers": [
                {"id": name, "pos_mm": [round(v, 1) for v in pos]} for name, pos in self.markers
            ],
        }

    def rebased(self, inv_transform: np.ndarray | None) -> RackObservation:
        """Re-express this observation in a transformed frame's coordinates.

        ``inv_transform`` maps the new frame's pixels back to the ones this
        observation was fitted in, so composing it with the homography gives a
        projection valid for detections made in the new frame.
        """
        if inv_transform is None or self.homography is None:
            return self
        return replace(self, homography=self.homography @ inv_transform)

    def project(self, point_px: tuple[float, float]) -> tuple[float, float, float] | None:
        """Image pixel -> rack millimetres. None when the rack is not locked.

        Returning None rather than a guess is deliberate: a distance predicate
        must degrade to "cannot tell" when the rack is lost, never assert a
        position derived from a stale or invented frame.
        """
        if self.homography is None:
            return None
        src = np.array([[[float(point_px[0]), float(point_px[1])]]], dtype=np.float32)
        dst = cv2.perspectiveTransform(src, self.homography)
        x, y = float(dst[0][0][0]), float(dst[0][0][1])
        return (x, y, 0.0)


class RackFrame:
    """Detects the rack fiducials and canonicalizes the input frame."""

    def __init__(self, layout: RackLayout | None = None) -> None:
        self.layout = layout or RackLayout()
        dict_id = _DICTS.get(self.layout.dictionary, cv2.aruco.DICT_4X4_50)
        self._detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(dict_id),
            cv2.aruco.DetectorParameters(),
        )
        #: Last good observation, so a one-frame dropout does not unlock the rack.
        self._last: RackObservation | None = None
        self._misses = 0
        #: Frames of consecutive loss tolerated before declaring the rack lost.
        self.hold_frames = 15

    # ------------------------------------------------------------------ detect

    def observe(self, frame: np.ndarray) -> RackObservation:
        """Find the rack in one frame, tolerating brief dropouts."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)

        centres: dict[int, tuple[float, float]] = {}
        if ids is not None:
            for marker_corners, marker_id in zip(corners, ids.flatten(), strict=False):
                pts = marker_corners.reshape(-1, 2)
                centres[int(marker_id)] = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))

        obs = self._build(centres)
        if obs.found:
            self._last, self._misses = obs, 0
            return obs

        # Degrade, do not drop. Holding the last lock across a brief occlusion
        # keeps a hand passing over the board from invalidating every position.
        self._misses += 1
        if self._last is not None and self._misses <= self.hold_frames:
            return self._last
        self._last = None
        return obs

    def _build(self, centres: dict[int, tuple[float, float]]) -> RackObservation:
        layout = self.layout
        seen = [cid for cid in layout.corner_ids if cid in centres]

        if len(seen) < 4:
            return RackObservation(
                found=False, rotation_deg=0.0, quality=0.0, corners_seen=len(seen)
            )

        src = np.array([centres[cid] for cid in layout.corner_ids], dtype=np.float32)
        homography, _ = cv2.findHomography(src, layout.corner_targets())
        if homography is None:
            return RackObservation(found=False, rotation_deg=0.0, quality=0.0, corners_seen=4)

        # Rack rotation in the image = angle of its top edge. This is what the
        # frame is de-rotated by, so the detector always sees an upright rack.
        (x0, y0), (x1, y1) = centres[layout.corner_ids[0]], centres[layout.corner_ids[1]]
        rotation = math.degrees(math.atan2(y1 - y0, x1 - x0))

        obs = RackObservation(
            found=True,
            rotation_deg=rotation,
            quality=1.0,
            homography=homography,
            corners_seen=4,
        )

        markers: list[tuple[str, tuple[float, float, float]]] = []
        for marker_id, name in layout.named.items():
            if marker_id not in centres:
                continue
            pos = obs.project(centres[marker_id])
            if pos is not None:
                markers.append((name, pos))

        return RackObservation(
            found=True,
            rotation_deg=rotation,
            quality=1.0,
            markers=tuple(markers),
            homography=homography,
            corners_seen=4,
        )

    # ------------------------------------------------------------ canonicalize

    @staticmethod
    def canonicalize(
        frame: np.ndarray, rotation_deg: float
    ) -> tuple[np.ndarray, float, np.ndarray | None]:
        """Rotate the input frame so the rack reads upright.

        Returns the frame, the rotation actually applied, and the inverse
        transform that maps a point in the rotated frame back to the original.
        That third value is what keeps positions honest: detections are made in
        the rotated frame, the homography was fitted in the original, and
        composing the two is the only way they agree.

        Below a couple of degrees we leave the frame alone -- resampling every
        frame to chase noise costs sharpness and buys nothing.
        """
        if abs(rotation_deg) < 2.0:
            return frame, 0.0, None
        h, w = frame.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rotation_deg, 1.0)
        rotated = cv2.warpAffine(
            frame, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        affine = np.vstack([matrix, [0.0, 0.0, 1.0]]).astype(np.float64)
        return rotated, rotation_deg, np.linalg.inv(affine)

    # ----------------------------------------------------------------- overlay

    @staticmethod
    def draw(frame: np.ndarray, obs: RackObservation) -> None:
        """Annotate lock state. Never hide a degraded state (invariant #10)."""
        if obs.found:
            text = f"RACK LOCKED  {obs.rotation_deg:+.0f}deg"
            colour = (0, 220, 120)
        else:
            text = f"RACK LOST ({obs.corners_seen}/4 fiducials)"
            colour = (0, 170, 255)
        cv2.putText(
            frame,
            text,
            (14, frame.shape[0] - 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            colour,
            2,
        )
