"""Short-gap tracking: stable ids, a moment's miss bridged, a real departure not."""

from __future__ import annotations

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.perception.tracking import COAST_S, ObjectTracker
from orbital_har.reasoning.engine import Engine, live_config
from orbital_har.reasoning.schema import Procedure

BOX = {"cls": "red_box", "conf": 0.9, "bbox": [100.0, 100.0, 160.0, 160.0]}


def nudged(obj: dict, dx: float) -> dict:
    x0, y0, x1, y1 = obj["bbox"]
    return {**obj, "bbox": [x0 + dx, y0, x1 + dx, y1]}


def test_an_object_keeps_its_id_as_it_moves() -> None:
    tr = ObjectTracker()
    first = tr.update([BOX], t=0.0)[0]["track_id"]
    assert [o["track_id"] for o in tr.update([nudged(BOX, 8)], t=0.1)] == [first]
    assert [o["track_id"] for o in tr.update([nudged(BOX, 16)], t=0.2)] == [first]


def test_a_moment_of_missing_is_bridged_at_the_last_place() -> None:
    tr = ObjectTracker()
    tid = tr.update([BOX], t=0.0)[0]["track_id"]
    gap = tr.update([], t=0.2)
    assert len(gap) == 1 and gap[0]["coasted"] and gap[0]["track_id"] == tid
    assert gap[0]["bbox"] == BOX["bbox"]
    back = tr.update([BOX], t=0.3)
    assert back[0]["track_id"] == tid and "coasted" not in back[0]


def test_an_object_taken_away_is_gone_within_the_coast() -> None:
    tr = ObjectTracker()
    tr.update([BOX], t=0.0)
    assert tr.update([], t=COAST_S - 0.05)  # still bridged
    assert tr.update([], t=COAST_S + 0.05) == []


def test_the_gap_is_seconds_not_frames() -> None:
    # At 4 FPS one missed frame is 0.25 s: bridged. Two are 0.5 s: gone.
    tr = ObjectTracker()
    tr.update([BOX], t=0.0)
    assert tr.update([], t=0.25)
    assert tr.update([], t=0.5) == []


def test_a_new_state_in_the_same_place_is_not_the_old_one_hiding() -> None:
    # Cap off: the trained model now says bottle_open where bottle_closed was.
    tr = ObjectTracker()
    closed = {"cls": "bottle_closed", "conf": 0.9, "bbox": [100.0, 100.0, 140.0, 200.0]}
    tr.update([closed], t=0.0)
    opened = {**closed, "cls": "bottle_open"}
    assert [o["cls"] for o in tr.update([opened], t=0.1)] == ["bottle_open"]


def test_two_objects_of_different_classes_get_their_own_ids() -> None:
    tr = ObjectTracker()
    other = {"cls": "yellow_box", "conf": 0.9, "bbox": [300.0, 100.0, 360.0, 160.0]}
    ids = {o["cls"]: o["track_id"] for o in tr.update([BOX, other], t=0.0)}
    assert ids["red_box"] != ids["yellow_box"]


# -------------------------------------------------------- why it matters


def _dwell_procedure() -> Procedure:
    return Procedure.model_validate(
        {
            "procedure": {"id": "d", "name": "d", "version": 1},
            "objects": [{"id": "red_box", "classes": ["red_box"], "color": "red"}],
            "regions": [{"id": "zone", "rect": [0.1, 0.1, 0.5, 0.5]}],
            "steps": [
                {
                    "id": "s1",
                    "name": "Leave the box in the zone",
                    "voice": "s1",
                    "requires": [{"dwell": "red_box", "region": "zone", "seconds": 2}],
                }
            ],
        }
    )


def _run(track: bool) -> StepState:
    """Five seconds of a box in its zone, lost for one frame every half second
    -- a hand passing over it -- at 15 FPS."""
    engine = Engine(_dwell_procedure(), live_config())
    tracker = ObjectTracker()
    for fid in range(1, 76):
        t = fid / 15
        seen = [] if fid % 8 == 0 else [BOX]
        objects = tracker.update(seen, t=t) if track else seen
        for i, (type_, payload) in enumerate(
            [
                (EventType.FRAME.value, {"frame_id": fid, "w": 640, "h": 480}),
                (EventType.DETECTION.value, {"frame_id": fid, "objects": objects}),
            ]
        ):
            engine.on_event(Event(t, fid * 10 + i, "test", type_, payload))
    return engine.state_of("s1")


def test_without_tracking_a_passing_hand_keeps_restarting_the_dwell() -> None:
    assert _run(track=False) != StepState.COMPLETE


def test_with_tracking_the_dwell_completes() -> None:
    assert _run(track=True) == StepState.COMPLETE
