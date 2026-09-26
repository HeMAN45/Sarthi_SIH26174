"""The drink-water procedure, judged from scene-state classifications.

A whole-frame classifier reports one class per frame (or nothing, when it sees
background). These tests replay such streams through the real engine, with the
configuration a live run uses, to prove the procedure itself is right before
anyone captures a photo: the order holds, one class can serve two steps, and
ambient state at the start completes nothing.
"""

from __future__ import annotations

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import make_engine, perception_needs
from tests.conftest import PROCEDURES

FPS = 15.0  # a laptop webcam through the classifier, roughly


def _proc() -> Procedure:
    return Procedure.load(PROCEDURES / "drink_water.yaml")


class Replay:
    """Feeds one classification per frame, the way the live loop emits them."""

    def __init__(self, mode: str = "clean") -> None:
        self.engine = make_engine(_proc(), mode)
        self.fid = 0
        self.alerts: list[tuple[str, str | None]] = []

    def show(self, cls: str | None, frames: int) -> None:
        for _ in range(frames):
            self.fid += 1
            t = self.fid / FPS
            objects = (
                [{"cls": cls, "conf": 0.93, "bbox": [0.0, 0.0, 640.0, 480.0], "track_id": None}]
                if cls
                else []
            )
            frame = {"frame_id": self.fid, "w": 640, "h": 480}
            detection = {"frame_id": self.fid, "objects": objects}
            for i, (src, type_, payload) in enumerate(
                (
                    ("capture", EventType.FRAME.value, frame),
                    ("detect", EventType.DETECTION.value, detection),
                )
            ):
                for out in self.engine.on_event(
                    Event(t=t, seq=self.fid * 10 + i, src=src, type=type_, payload=payload)
                ):
                    if out.type == EventType.ALERT.value:
                        self.alerts.append((out.payload["kind"], out.payload.get("step_id")))

    def state(self, step_id: str) -> StepState:
        return self.engine.state_of(step_id)


def test_the_procedure_validates_and_needs_no_rack_or_pose() -> None:
    proc = _proc()
    assert [s.id for s in proc.steps] == ["s1", "s2", "s3", "s4", "s5"]
    assert proc.vocabulary_classes == {"bottle_home", "holding_closed", "holding_open", "drinking"}
    assert perception_needs(proc) == (False, False)


def test_a_correct_run_completes_every_step_in_order() -> None:
    r = Replay()
    r.show("bottle_home", 20)
    r.show("holding_closed", 14)
    r.show("holding_open", 14)
    r.show("drinking", 22)
    r.show("holding_closed", 14)
    r.show("bottle_home", 18)

    assert r.engine.is_complete
    assert all(r.state(s) == StepState.COMPLETE for s in ("s1", "s2", "s3", "s4", "s5"))
    assert r.alerts == []


def test_the_bottle_at_home_before_the_run_completes_nothing() -> None:
    """Ambient state is not evidence (invariant #11): 'put it back' is true
    before anyone picked it up, and must not fire on frame one."""
    r = Replay()
    r.show("bottle_home", 60)
    assert r.state("s5") != StepState.COMPLETE
    assert not any(r.state(s) == StepState.COMPLETE for s in ("s1", "s2", "s3", "s4", "s5"))


def test_one_class_serves_two_steps_without_completing_both_at_once() -> None:
    r = Replay()
    r.show("holding_closed", 30)
    assert r.state("s1") == StepState.COMPLETE
    assert r.state("s4") != StepState.COMPLETE


def test_background_frames_complete_nothing() -> None:
    """What a finger or a face should look like once background covers them:
    the classifier reports nothing, and the step stays open."""
    r = Replay()
    r.show(None, 90)
    assert not any(r.state(s) == StepState.COMPLETE for s in ("s1", "s2", "s3", "s4", "s5"))


def test_a_flicker_is_not_a_step() -> None:
    r = Replay()
    for _ in range(10):
        r.show("holding_closed", 3)
        r.show(None, 2)
    assert r.state("s1") != StepState.COMPLETE


def test_strict_mode_flags_drinking_before_opening() -> None:
    r = Replay("strict")
    r.show("holding_closed", 14)
    assert r.state("s1") == StepState.COMPLETE
    r.show("drinking", 22)
    assert r.state("s3") == StepState.OUT_OF_ORDER
    assert ("out_of_order", "s3") in r.alerts
