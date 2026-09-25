"""Perception output drives the reasoning engine.

The seam these two halves meet at is the event bus, so this is the test that
matters most: real rack localisation and real contact inference, turned into
real events, satisfying the real PROC-A predicates -- with no camera, no
detector and no network anywhere in the loop.

Before this existed, PROC-A could not run on a camera at all: nothing emitted
rack or contact events, so four of its six steps were unsatisfiable.
"""

from __future__ import annotations

import cv2
import numpy as np

from orbital_har.core.types import EventType, StepState
from orbital_har.perception.hands import ContactInferrer, HandPoint
from orbital_har.perception.rackframe import RackFrame
from orbital_har.reasoning.engine import Engine
from orbital_har.reasoning.schema import Procedure
from tests.test_perception import CANVAS, CORNER_PX, _board

FPS = 30.0
#: Pixel homes for the two named fiducials and the props that visit them.
R_PX = (450, 150)
Y_PX = (450, 550)
STOW_PX = (300, 350)


def _locked_rack() -> RackFrame:
    return RackFrame()


def _obs():
    rack = _locked_rack()
    obs = rack.observe(_board(extra={10: R_PX, 11: Y_PX}))
    assert obs.found, "the synthetic board must lock, or the test proves nothing"
    return obs


def _obj(cls: str, centre_px: tuple[float, float], obs, size: float = 60.0) -> dict:
    cx, cy = centre_px
    bbox = [cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2]
    out = {"cls": cls, "conf": 0.92, "bbox": bbox, "track_id": None}
    pos = obs.project((cx, cy))
    if pos is not None:
        out["centroid_mm"] = [round(v, 1) for v in pos]
    return out


def _feed(engine: Engine, obs, objects: list[dict], hands=(), *, frames: int, start: int):
    """Publish ``frames`` identical frames the way the live loop would."""
    emitted = []
    pairs = ContactInferrer().infer(tuple(hands), objects)
    for i in range(frames):
        fid = start + i
        t = fid / FPS
        seq = fid * 10
        emitted += engine.on_event(
            _ev(
                t,
                seq + 1,
                "capture",
                EventType.FRAME.value,
                {"frame_id": fid, "w": CANVAS[0], "h": CANVAS[1]},
            )
        )
        emitted += engine.on_event(
            _ev(t, seq + 2, "rackframe", EventType.RACK.value, obs.to_payload(fid))
        )
        emitted += engine.on_event(
            _ev(
                t,
                seq + 3,
                "detect",
                EventType.DETECTION.value,
                {"frame_id": fid, "objects": objects},
            )
        )
        if pairs:
            emitted += engine.on_event(
                _ev(t, seq + 4, "hands", EventType.CONTACT.value, {"frame_id": fid, "pairs": pairs})
            )
    return emitted


def _ev(t, seq, src, type_, payload):
    from orbital_har.core.types import Event

    return Event(t=t, seq=seq, src=src, type=type_, payload=payload)


# --------------------------------------------------------------------------


def test_the_board_locks_and_names_both_markers() -> None:
    named = dict(_obs().markers)
    assert set(named) == {"R", "Y"}


def test_detections_carry_rack_frame_positions() -> None:
    obs = _obs()
    box = _obj("red_box_closed", R_PX, obs)
    assert "centroid_mm" in box
    marker = dict(obs.markers)["R"]
    # The prop sits on the marker, so their rack coordinates must agree.
    assert abs(box["centroid_mm"][0] - marker[0]) < 15.0
    assert abs(box["centroid_mm"][1] - marker[1]) < 15.0


def test_perception_events_complete_proc_a_step_one(proc_a: Procedure) -> None:
    obs = _obs()
    engine = Engine(proc_a)

    _feed(engine, obs, [_obj("outer_box_open", STOW_PX, obs)], frames=20, start=0)

    assert engine.state_of("s1") is StepState.COMPLETE
    assert engine.state_of("s2") is StepState.ACTIVE


def test_contact_and_placement_complete_proc_a_step_two(proc_a: Procedure) -> None:
    """s2 needs BOTH a hand-object contact and a rack-frame placement.

    Neither was obtainable from a camera before perception existed. This is the
    step that proves the sample experiment is now live.
    """
    obs = _obs()
    engine = Engine(proc_a)
    _feed(engine, obs, [_obj("outer_box_open", STOW_PX, obs)], frames=20, start=0)
    assert engine.state_of("s2") is StepState.ACTIVE

    on_marker = [
        _obj("outer_box_open", STOW_PX, obs),
        _obj("red_box_closed", R_PX, obs),
    ]
    hand = (HandPoint("right", float(R_PX[0]), float(R_PX[1]), 0.9),)
    _feed(engine, obs, on_marker, hand, frames=20, start=20)

    assert engine.state_of("s2") is StepState.COMPLETE


def test_placement_away_from_the_marker_does_not_complete(proc_a: Procedure) -> None:
    """max_mm is 120. Put the box on the wrong side of the rack."""
    obs = _obs()
    engine = Engine(proc_a)
    _feed(engine, obs, [_obj("outer_box_open", STOW_PX, obs)], frames=20, start=0)

    wrong = [
        _obj("outer_box_open", STOW_PX, obs),
        _obj("red_box_closed", Y_PX, obs),  # marker Y, not R
    ]
    hand = (HandPoint("right", float(Y_PX[0]), float(Y_PX[1]), 0.9),)
    _feed(engine, obs, wrong, hand, frames=20, start=20)

    assert engine.state_of("s2") is not StepState.COMPLETE


def test_losing_the_rack_withholds_placement_rather_than_guessing(
    proc_a: Procedure,
) -> None:
    """Invariant: degrade, never assert. No lock means no position claim."""
    obs = _obs()
    engine = Engine(proc_a)
    _feed(engine, obs, [_obj("outer_box_open", STOW_PX, obs)], frames=20, start=0)

    blank = np.full((CANVAS[1], CANVAS[0], 3), 255, dtype=np.uint8)
    rack = _locked_rack()
    rack.observe(_board(extra={10: R_PX, 11: Y_PX}))
    for _ in range(rack.hold_frames + 2):
        lost = rack.observe(blank)
    assert not lost.found

    # The box IS on the marker, but with no lock we cannot know that.
    objects = [_obj("outer_box_open", STOW_PX, obs), _obj("red_box_closed", R_PX, lost)]
    hand = (HandPoint("right", float(R_PX[0]), float(R_PX[1]), 0.9),)
    _feed(engine, lost, objects, hand, frames=20, start=20)

    assert engine.state_of("s2") is not StepState.COMPLETE


def test_a_tilted_rack_still_completes_the_step(proc_a: Procedure) -> None:
    """Canonicalization plus rebasing: the camera is crooked, the verdict is not."""
    tilted = _board(extra={10: R_PX, 11: Y_PX}, rotate_deg=18.0)
    raw = RackFrame().observe(tilted)
    assert raw.found
    _, _, inverse = RackFrame.canonicalize(tilted, raw.rotation_deg)
    obs = raw.rebased(inverse)

    # Where the props land once the frame has been straightened.
    matrix = cv2.getRotationMatrix2D((CANVAS[0] / 2, CANVAS[1] / 2), raw.rotation_deg, 1.0)

    def moved(px):
        p = matrix @ np.array([float(px[0]), float(px[1]), 1.0])
        return (float(p[0]), float(p[1]))

    engine = Engine(proc_a)
    _feed(engine, obs, [_obj("outer_box_open", moved(STOW_PX), obs)], frames=20, start=0)
    assert engine.state_of("s1") is StepState.COMPLETE

    r_px = moved(R_PX)
    objects = [_obj("outer_box_open", moved(STOW_PX), obs), _obj("red_box_closed", r_px, obs)]
    hand = (HandPoint("right", r_px[0], r_px[1], 0.9),)
    _feed(engine, obs, objects, hand, frames=20, start=20)

    assert engine.state_of("s2") is StepState.COMPLETE


def test_the_whole_run_touches_no_network(proc_a: Procedure, monkeypatch) -> None:
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("outbound network call during perception")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

    obs = _obs()
    engine = Engine(proc_a)
    _feed(engine, obs, [_obj("outer_box_open", STOW_PX, obs)], frames=20, start=0)
    assert engine.state_of("s1") is StepState.COMPLETE

    if CORNER_PX:  # keep the import meaningful
        assert len(CORNER_PX) == 4
