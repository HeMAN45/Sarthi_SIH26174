"""The sample experiment at a bench, worked continuously, replayed through the
live engine configuration.

Nothing here is held up to the camera and no gesture says "done". The operator
moves boxes between taped zones and each step is judged by where things end up
and by a hand having handled them -- including when one action flows straight
into the next, as real work does.
"""

from __future__ import annotations

import pytest

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import make_engine, perception_needs
from tests.conftest import PROCEDURES

# 640x480, zones as in the YAML: stowage x 19-205, zone R x 224-410, zone Y x 429-621,
# all y 264-470.
STOW_RED = (60, 300, 120, 360)
STOW_YELLOW = (120, 380, 180, 440)
ZONE_R = (290, 320, 350, 380)
ZONE_Y = (500, 320, 560, 380)
IN_TRANSIT = (330, 150, 390, 210)  # lifted, above the bench


def inside(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """The small sample sitting in a box."""
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return (cx - 8, cy - 8, cx + 8, cy + 8)


class Bench:
    """Frames of colour-found objects at positions, and which ones a hand is on."""

    def __init__(self, fps: float = 15.0, mode: str = "clean") -> None:
        self.engine = make_engine(Procedure.load(PROCEDURES / "bench_sample.yaml"), mode)
        self.fps = fps
        self.fid = 0
        self.at: dict[str, tuple[int, int, int, int] | None] = {
            "red_box": STOW_RED,
            "yellow_box": STOW_YELLOW,
            "sample": inside(STOW_RED),
        }
        self.alerts: list[tuple[str, str | None]] = []

    def run(self, seconds: float, touch: tuple[str, ...] = ()) -> None:
        for _ in range(max(1, round(seconds * self.fps))):
            self.fid += 1
            t = self.fid / self.fps
            objs = [
                {"cls": c, "conf": 0.9, "bbox": list(b)}
                for c, b in self.at.items()
                if b is not None
            ]
            events = [
                (EventType.FRAME.value, {"frame_id": self.fid, "w": 640, "h": 480}),
                (EventType.DETECTION.value, {"frame_id": self.fid, "objects": objs}),
            ]
            pairs = [{"a": "hand", "side": "right", "cls": c, "conf": 0.9} for c in touch]
            if pairs:
                events.append((EventType.CONTACT.value, {"frame_id": self.fid, "pairs": pairs}))
            for i, (type_, payload) in enumerate(events):
                for out in self.engine.on_event(Event(t, self.fid * 10 + i, "t", type_, payload)):
                    if out.type == EventType.ALERT.value:
                        self.alerts.append((out.payload["kind"], out.payload.get("step_id")))

    def carry(self, obj: str, to: tuple[int, int, int, int], *, with_sample: bool = False) -> None:
        """Pick ``obj`` up, carry it, set it down, let go: about 1.2 s of handling."""
        self.run(0.3, touch=(obj,))
        self.at[obj] = IN_TRANSIT
        if with_sample:
            self.at["sample"] = inside(IN_TRANSIT)
        self.run(0.6, touch=(obj,))
        self.at[obj] = to
        if with_sample:
            self.at["sample"] = inside(to)
        self.run(0.3, touch=(obj,))

    def state(self, step: str) -> StepState:
        return self.engine.state_of(step)

    def done(self, *steps: str) -> bool:
        return all(self.state(s) == StepState.COMPLETE for s in steps)


def test_it_is_a_scene_with_body_tracking_and_no_training() -> None:
    proc = Procedure.load(PROCEDURES / "bench_sample.yaml")
    assert proc.is_scene
    assert perception_needs(proc) == (False, True)  # hands from pose, no rack
    assert proc.colour_classes == {"red_box": "red", "yellow_box": "yellow", "sample": "blue"}


@pytest.mark.parametrize("fps", [15.0, 4.0])
def test_a_continuous_run_with_no_pauses_completes_cleanly(fps: float) -> None:
    b = Bench(fps)
    b.run(1.0)  # both boxes in stowage: nothing is done yet
    assert not any(b.done(s) for s in ("s1", "s2", "s3", "s4"))

    # Each action flows straight into the next: the yellow box is picked up
    # while the red one is still settling in zone R.
    b.carry("red_box", ZONE_R, with_sample=True)
    b.carry("yellow_box", ZONE_Y)
    b.run(2.0)
    assert b.done("s1", "s2")

    b.run(0.3, touch=("sample",))
    b.at["sample"] = inside(ZONE_Y)
    b.run(0.3, touch=("sample",))
    b.carry("red_box", STOW_RED)
    b.carry("yellow_box", STOW_YELLOW)
    b.at["sample"] = inside(STOW_YELLOW)
    b.run(2.0)
    assert b.done("s3", "s4")
    assert b.engine.is_complete
    assert b.alerts == []


def test_the_yellow_box_first_skips_the_red_one() -> None:
    b = Bench()
    b.carry("yellow_box", ZONE_Y)
    b.run(2.0)
    assert b.done("s2")
    assert b.state("s1") == StepState.SKIPPED
    assert b.alerts == [("skip", "s1")]


def test_picking_up_the_sample_too_early_is_a_wrong_object() -> None:
    b = Bench()
    b.run(3.0)  # past the grace for what was in view when the step began
    b.run(1.0, touch=("sample",))
    assert ("wrong_object", "s3") in b.alerts
    assert not b.done("s3")


def test_stowing_the_boxes_before_the_transfer_skips_it() -> None:
    b = Bench()
    b.carry("red_box", ZONE_R, with_sample=True)
    b.carry("yellow_box", ZONE_Y)
    b.run(2.0)
    assert b.done("s1", "s2")
    b.carry("red_box", STOW_RED, with_sample=True)  # the sample never left it
    b.carry("yellow_box", STOW_YELLOW)
    b.run(2.0)
    assert b.done("s4")
    assert b.state("s3") == StepState.SKIPPED
    assert b.alerts == [("skip", "s3")]


def test_strict_mode_holds_the_early_step_out_of_sequence() -> None:
    b = Bench(mode="strict")
    b.carry("yellow_box", ZONE_Y)
    b.run(2.0)
    assert b.state("s2") == StepState.OUT_OF_ORDER
    assert b.alerts == [("out_of_order", "s2")]


def test_a_box_that_was_never_handled_does_not_count() -> None:
    # A box already sitting in zone R at the start is the resting scene, not
    # something the operator did: the step needs a hand on it.
    b = Bench()
    b.at["red_box"] = ZONE_R
    b.at["sample"] = inside(ZONE_R)
    b.run(4.0)
    assert not b.done("s1")
