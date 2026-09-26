"""The drink-water experiment, both forms, replayed through the real engine.

``drink_water.yaml`` learns only the cap state (``bottle_open`` /
``bottle_closed``, trained in Objects mode) and measures the rest: a hand on
the bottle, a hand at the face, the bottle standing in its home spot.
``drink_water_body.yaml`` needs no training at all. These tests feed the
events a live run would emit, with the configuration a live run uses, to prove
the procedures are right before anyone captures a photo.
"""

from __future__ import annotations

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import make_engine, perception_needs
from tests.conftest import PROCEDURES

FPS = 15.0  # a laptop webcam through detection and pose, roughly

HOME = (460, 300, 520, 440)  # inside the home rect, 640x480 frame
AWAY = (250, 120, 310, 260)  # held up in front of the chest


class Replay:
    """Frames carrying boxes, hand contact and body gestures, as the loop emits them."""

    def __init__(self, name: str, mode: str = "clean") -> None:
        self.engine = make_engine(Procedure.load(PROCEDURES / name), mode)
        self.fid = 0
        self.alerts: list[tuple[str, str | None]] = []

    def scene(
        self,
        frames: int,
        *,
        at: tuple[int, int, int, int] | None = None,
        seen: tuple[str, ...] = ("bottle",),
        touch: bool = False,
        gestures: tuple[str, ...] = (),
    ) -> None:
        """``seen`` are the classes reported for the bottle's box at ``at``."""
        for _ in range(frames):
            self.fid += 1
            t = self.fid / FPS
            objs = [] if at is None else [{"cls": c, "conf": 0.9, "bbox": list(at)} for c in seen]
            events = [
                ("capture", EventType.FRAME.value, {"frame_id": self.fid, "w": 640, "h": 480}),
                ("detect", EventType.DETECTION.value, {"frame_id": self.fid, "objects": objs}),
            ]
            if touch and at is not None:
                pairs = [{"a": "hand", "side": "right", "cls": c, "conf": 0.9} for c in seen]
                events.append(
                    ("hands", EventType.CONTACT.value, {"frame_id": self.fid, "pairs": pairs})
                )
            if gestures:
                body = {
                    "frame_id": self.fid,
                    "gestures": [{"name": g, "side": "both", "conf": 0.9} for g in gestures],
                }
                events.append(("gestures", EventType.GESTURE.value, body))
            for i, (src, type_, payload) in enumerate(events):
                for out in self.engine.on_event(Event(t, self.fid * 10 + i, src, type_, payload)):
                    if out.type == EventType.ALERT.value:
                        self.alerts.append((out.payload["kind"], out.payload.get("step_id")))

    def state(self, step_id: str) -> StepState:
        return self.engine.state_of(step_id)

    def done(self, *step_ids: str) -> bool:
        return all(self.state(s) == StepState.COMPLETE for s in step_ids)


STEPS = ("s1", "s2", "s3", "s4", "s5")
CLOSED = ("bottle", "bottle_closed")  # the stock model and yours, on one bottle
OPEN = ("bottle", "bottle_open")


# ------------------------------------------------------ the trained version


def test_trained_version_learns_only_the_cap_state() -> None:
    proc = Procedure.load(PROCEDURES / "drink_water.yaml")
    assert [s.id for s in proc.steps] == list(STEPS)
    assert proc.vocabulary_classes == {"bottle", "bottle_closed", "bottle_open"}
    assert perception_needs(proc) == (False, True)  # body tracking, no rack
    assert proc.is_scene  # the bottle at home is small in frame and must still count


def test_trained_version_completes_a_correct_run_in_order() -> None:
    r = Replay("drink_water.yaml")
    r.scene(20, at=HOME, seen=CLOSED)  # standing at home: completes nothing
    assert not any(r.done(s) for s in STEPS)
    r.scene(8, at=HOME, seen=CLOSED, touch=True)
    assert r.done("s1")
    r.scene(10, at=AWAY, seen=OPEN, touch=True)
    assert r.done("s2")
    r.scene(16, at=AWAY, seen=OPEN, gestures=("hand_to_face",))
    assert r.done("s3")
    r.scene(10, at=AWAY, seen=CLOSED, touch=True)
    assert r.done("s4")
    r.scene(45, at=HOME, seen=CLOSED)  # three seconds in the home spot
    assert r.engine.is_complete and r.alerts == []


def test_scratching_your_face_is_not_drinking() -> None:
    r = Replay("drink_water.yaml")
    r.scene(8, at=HOME, seen=CLOSED, touch=True)
    r.scene(10, at=AWAY, seen=OPEN, touch=True)
    r.scene(30, gestures=("hand_to_face",))  # bottle put down out of view
    assert not r.done("s3")


def test_a_flicker_of_the_open_cap_is_not_opening_it() -> None:
    r = Replay("drink_water.yaml")
    r.scene(8, at=HOME, seen=CLOSED, touch=True)
    for _ in range(8):
        r.scene(2, at=AWAY, seen=OPEN)
        r.scene(3, at=AWAY, seen=CLOSED)
    assert not r.done("s2")


def test_strict_mode_flags_putting_it_back_before_closing_the_cap() -> None:
    r = Replay("drink_water.yaml", "strict")
    r.scene(8, at=HOME, seen=CLOSED, touch=True)
    r.scene(10, at=AWAY, seen=OPEN, touch=True)
    r.scene(16, at=AWAY, seen=OPEN, gestures=("hand_to_face",))
    assert r.done("s1", "s2", "s3")
    r.scene(45, at=HOME, seen=OPEN)  # back home with the cap still off
    assert r.state("s5") == StepState.OUT_OF_ORDER
    assert ("out_of_order", "s5") in r.alerts


# ------------------------------------------------ the body-tracking version


def test_body_version_needs_pose_but_no_rack_and_no_training() -> None:
    proc = Procedure.load(PROCEDURES / "drink_water_body.yaml")
    assert perception_needs(proc) == (False, True)
    assert proc.vocabulary_classes == {"bottle"}  # the stock detector knows it


def test_body_version_completes_a_correct_run_in_order() -> None:
    r = Replay("drink_water_body.yaml")
    r.scene(20, at=HOME)  # standing at home: completes nothing
    assert not any(r.done(s) for s in ("s1", "s5"))
    r.scene(8, at=HOME, touch=True)
    assert r.done("s1")
    r.scene(14, at=AWAY, touch=True, gestures=("hands_together",))
    assert r.done("s2")
    r.scene(16, at=AWAY, gestures=("hand_to_face",))
    assert r.done("s3")
    r.scene(14, at=AWAY, touch=True, gestures=("hands_together",))
    assert r.done("s4")
    r.scene(45, at=HOME)  # three seconds in the home spot
    assert r.engine.is_complete and r.alerts == []


def test_body_version_does_not_take_a_face_scratch_for_a_drink() -> None:
    r = Replay("drink_water_body.yaml")
    r.scene(8, at=HOME, touch=True)
    r.scene(14, at=AWAY, touch=True, gestures=("hands_together",))
    r.scene(30, gestures=("hand_to_face",))
    assert not r.done("s3")
