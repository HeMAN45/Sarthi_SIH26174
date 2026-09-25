"""Rack localisation and contact inference.

These run with no camera and no network: the rack board is synthesised from the
ArUco dictionary itself, so the test exercises the real detector against real
fiducials rather than a mock.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from orbital_har.perception.hands import (
    ContactInferrer,
    HandPoint,
    HandTracker,
    _box_gap,
    _point_box_distance,
)
from orbital_har.perception.pose import Keypoint, PersonPose, PoseObservation
from orbital_har.perception.rackframe import RackFrame, RackLayout

MARKER_PX = 70
CANVAS = (900, 700)  # w, h

#: Where each corner fiducial is drawn, matching RackLayout.corner_ids order.
CORNER_PX = {0: (150, 150), 1: (750, 150), 2: (750, 550), 3: (150, 550)}


def _board(extra: dict[int, tuple[int, int]] | None = None, rotate_deg: float = 0.0) -> np.ndarray:
    """Draw a rack board. White background gives the detector its quiet zone."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = np.full((CANVAS[1], CANVAS[0], 3), 255, dtype=np.uint8)
    for marker_id, (cx, cy) in {**CORNER_PX, **(extra or {})}.items():
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, MARKER_PX)
        patch = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        x0, y0 = cx - MARKER_PX // 2, cy - MARKER_PX // 2
        img[y0 : y0 + MARKER_PX, x0 : x0 + MARKER_PX] = patch
    if rotate_deg:
        matrix = cv2.getRotationMatrix2D((CANVAS[0] / 2, CANVAS[1] / 2), rotate_deg, 1.0)
        img = cv2.warpAffine(img, matrix, CANVAS, borderValue=(255, 255, 255))
    return img


# --------------------------------------------------------------------------
# Rack frame
# --------------------------------------------------------------------------


def test_rack_locks_when_all_four_fiducials_are_visible() -> None:
    obs = RackFrame().observe(_board())
    assert obs.found
    assert obs.corners_seen == 4
    assert obs.homography is not None


def test_rack_does_not_lock_on_a_blank_frame() -> None:
    blank = np.full((CANVAS[1], CANVAS[0], 3), 255, dtype=np.uint8)
    obs = RackFrame().observe(blank)
    assert not obs.found
    assert obs.project((100.0, 100.0)) is None


def test_projection_maps_pixels_to_rack_millimetres() -> None:
    layout = RackLayout()
    obs = RackFrame(layout).observe(_board())

    # The board's centre in pixels is the rack's centre in millimetres.
    centre_px = (
        (CORNER_PX[0][0] + CORNER_PX[2][0]) / 2,
        (CORNER_PX[0][1] + CORNER_PX[2][1]) / 2,
    )
    pos = obs.project(centre_px)
    assert pos is not None
    assert pos[0] == pytest.approx(layout.width_mm / 2, abs=8.0)
    assert pos[1] == pytest.approx(layout.height_mm / 2, abs=8.0)


def test_named_markers_are_reported_in_rack_millimetres() -> None:
    """Marker R sits mid-way along the rack's top edge."""
    top_mid_px = ((CORNER_PX[0][0] + CORNER_PX[1][0]) // 2, CORNER_PX[0][1])
    obs = RackFrame().observe(_board(extra={10: top_mid_px}))

    named = dict(obs.markers)
    assert "R" in named
    assert named["R"][0] == pytest.approx(RackLayout().width_mm / 2, abs=10.0)
    assert named["R"][1] == pytest.approx(0.0, abs=10.0)


def test_rotation_is_measured_from_the_rack_top_edge() -> None:
    upright = RackFrame().observe(_board())
    assert upright.rotation_deg == pytest.approx(0.0, abs=2.0)

    tilted = RackFrame().observe(_board(rotate_deg=20.0))
    assert tilted.found
    # warpAffine rotates the image counter-clockwise, so the edge angle is -20.
    assert abs(tilted.rotation_deg) == pytest.approx(20.0, abs=3.0)


def test_canonicalization_returns_the_rack_upright() -> None:
    tilted = _board(rotate_deg=20.0)
    obs = RackFrame().observe(tilted)
    corrected, applied, _ = RackFrame.canonicalize(tilted, obs.rotation_deg)

    assert applied != 0.0
    assert RackFrame().observe(corrected).rotation_deg == pytest.approx(0.0, abs=3.0)


def test_tiny_rotations_are_left_alone() -> None:
    """Resampling every frame to chase noise costs sharpness and buys nothing."""
    frame = _board()
    out, applied, inverse = RackFrame.canonicalize(frame, 1.2)
    assert applied == 0.0
    assert inverse is None
    assert out is frame


def test_rebasing_keeps_positions_correct_across_canonicalization() -> None:
    """The whole point of the inverse transform: one point, one answer.

    A detection made in the rotated frame must project to the same rack
    millimetres as the same physical point did in the original frame.
    """
    tilted = _board(rotate_deg=20.0)
    obs = RackFrame().observe(tilted)
    centre_px = (
        (CORNER_PX[0][0] + CORNER_PX[2][0]) / 2,
        (CORNER_PX[0][1] + CORNER_PX[2][1]) / 2,
    )
    before = obs.project(centre_px)

    rotated, _, inverse = RackFrame.canonicalize(tilted, obs.rotation_deg)
    rebased = obs.rebased(inverse)

    # Where the board centre landed after rotating about the image centre.
    matrix = cv2.getRotationMatrix2D((CANVAS[0] / 2, CANVAS[1] / 2), obs.rotation_deg, 1.0)
    moved = matrix @ np.array([centre_px[0], centre_px[1], 1.0])
    after = rebased.project((float(moved[0]), float(moved[1])))

    assert before is not None and after is not None
    assert after[0] == pytest.approx(before[0], abs=2.0)
    assert after[1] == pytest.approx(before[1], abs=2.0)
    assert rotated.shape == tilted.shape


def test_a_brief_dropout_holds_the_previous_lock() -> None:
    """A hand passing over the board must not invalidate every position."""
    rack = RackFrame()
    assert rack.observe(_board()).found

    blank = np.full((CANVAS[1], CANVAS[0], 3), 255, dtype=np.uint8)
    assert rack.observe(blank).found, "one dropped frame should not unlock"


def test_a_sustained_dropout_finally_unlocks() -> None:
    """Degrade, but never pretend. Held forever would be a lie."""
    rack = RackFrame()
    rack.observe(_board())
    blank = np.full((CANVAS[1], CANVAS[0], 3), 255, dtype=np.uint8)

    for _ in range(rack.hold_frames + 1):
        last = rack.observe(blank)
    assert not last.found


# --------------------------------------------------------------------------
# Contact geometry
# --------------------------------------------------------------------------


BOX = (100.0, 100.0, 200.0, 200.0)


def test_point_inside_a_box_is_zero_distance() -> None:
    assert _point_box_distance((150.0, 150.0), BOX) == 0.0


def test_point_outside_a_box_measures_the_gap() -> None:
    assert _point_box_distance((230.0, 150.0), BOX) == pytest.approx(30.0)


def test_overlapping_boxes_have_no_gap() -> None:
    assert _box_gap(BOX, (150.0, 150.0, 250.0, 250.0)) == 0.0


def _objects(*boxes: tuple[str, tuple[float, float, float, float]]) -> list[dict]:
    return [
        {"cls": cls, "conf": 0.9, "bbox": list(bbox), "track_id": i}
        for i, (cls, bbox) in enumerate(boxes)
    ]


def test_hand_on_an_object_produces_a_contact() -> None:
    hands = (HandPoint("right", 150.0, 150.0, 0.9),)
    pairs = ContactInferrer().infer(hands, _objects(("red_box_open", BOX)))

    hand_pairs = [p for p in pairs if p["a"] == "hand"]
    assert len(hand_pairs) == 1
    assert hand_pairs[0]["cls"] == "red_box_open"
    assert hand_pairs[0]["conf"] > 0.8


def test_a_distant_hand_produces_no_contact() -> None:
    hands = (HandPoint("right", 600.0, 600.0, 0.9),)
    assert ContactInferrer().infer(hands, _objects(("red_box_open", BOX))) == []


def test_contact_confidence_falls_off_with_distance() -> None:
    """Graded evidence, not a boolean -- abstention depends on it."""
    near = ContactInferrer().infer(
        (HandPoint("right", 205.0, 150.0, 1.0),), _objects(("red_box_open", BOX))
    )
    far = ContactInferrer().infer(
        (HandPoint("right", 215.0, 150.0, 1.0),), _objects(("red_box_open", BOX))
    )
    assert near[0]["conf"] > far[0]["conf"]


def test_touching_objects_produce_contacts_in_both_directions() -> None:
    """So a procedure may write the pair in whichever order reads naturally."""
    pairs = ContactInferrer().infer(
        (), _objects(("tweezers", BOX), ("sample_vial", (195.0, 100.0, 295.0, 200.0)))
    )
    directions = {(p["a"], p["cls"]) for p in pairs}
    assert ("tweezers", "sample_vial") in directions
    assert ("sample_vial", "tweezers") in directions


def test_separated_objects_produce_no_contact() -> None:
    pairs = ContactInferrer().infer(
        (), _objects(("tweezers", BOX), ("sample_vial", (600.0, 600.0, 700.0, 700.0)))
    )
    assert pairs == []


# --------------------------------------------------------------------------
# Hands from pose
# --------------------------------------------------------------------------


def _person(**kp: tuple[float, float, float]) -> PersonPose:
    points = [Keypoint(0.0, 0.0, 0.0) for _ in range(17)]
    for name, (x, y, c) in kp.items():
        points[{"l_elbow": 7, "r_elbow": 8, "l_wrist": 9, "r_wrist": 10}[name]] = Keypoint(x, y, c)
    return PersonPose(keypoints=tuple(points), conf=0.9)


def test_hand_point_is_pushed_past_the_wrist_onto_the_palm() -> None:
    person = _person(r_elbow=(100.0, 100.0, 0.9), r_wrist=(200.0, 100.0, 0.9))
    hands = HandTracker().observe(PoseObservation(people=(person,)))

    right = next(h for h in hands if h.side == "right")
    assert right.x > 200.0, "palm sits beyond the wrist joint"
    assert right.y == pytest.approx(100.0)


def test_a_wrist_without_an_elbow_still_yields_a_hand() -> None:
    person = _person(r_wrist=(200.0, 100.0, 0.9))
    hands = HandTracker().observe(PoseObservation(people=(person,)))
    assert [h.xy for h in hands] == [(200.0, 100.0)]


def test_low_confidence_wrists_are_dropped() -> None:
    person = _person(r_wrist=(200.0, 100.0, 0.05))
    assert HandTracker().observe(PoseObservation(people=(person,))) == ()


def test_no_person_means_no_hands() -> None:
    assert HandTracker().observe(PoseObservation()) == ()
