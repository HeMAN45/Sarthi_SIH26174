"""The seed-germination demo, replayed through the live engine configuration."""

from __future__ import annotations

from orbital_har.core.types import StepState
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import perception_needs
from tests.conftest import PROCEDURES
from tests.test_drink_water import Replay

CHAMBER = (460, 300, 540, 440)  # inside the growth-chamber rect, 640x480
BENCH = (200, 200, 300, 330)  # out on the bench in front of the operator
HAND = (320, 120, 380, 220)  # whatever is held up


def pot(r: Replay, frames: int, at=BENCH, touch: bool = False) -> None:
    r.scene(frames, at=at, seen=("potted plant",), touch=touch)


def held(r: Replay, frames: int, cls: str) -> None:
    """Holding ``cls`` while the pot stands on the bench."""
    for _ in range(frames):
        r.scene(1, at=HAND, seen=(cls,), touch=True)


def test_the_procedure_is_a_scene_with_body_tracking() -> None:
    proc = Procedure.load(PROCEDURES / "seed_germination.yaml")
    assert perception_needs(proc) == (False, True)
    assert proc.is_scene


def test_a_correct_check_completes_every_step() -> None:
    r = Replay("seed_germination.yaml")
    pot(r, 20, at=CHAMBER)
    assert not any(r.done(s) for s in ("s1", "s4"))
    pot(r, 8, touch=True)
    assert r.done("s1")
    for _ in range(8):  # watering: bottle in hand, pot on the bench
        r.scene(1, at=HAND, seen=("bottle",), touch=True)
        r.scene(1, at=BENCH, seen=("potted plant",))
    assert not r.done("s2")  # the hand left the bottle every other frame
    for _ in range(8):
        r.scene(1, at=HAND, seen=("bottle", "potted plant"), touch=True)
    assert r.done("s2")
    held(r, 8, "cell phone")
    assert r.done("s3")
    pot(r, 45, at=CHAMBER)
    assert r.done("s4")
    r.scene(8, gestures=("both_hands_raised",))
    assert r.engine.is_complete and r.alerts == []


def test_photographing_before_watering_skips_the_watering() -> None:
    # Clean mode judges the next step too, so taking the photo while the
    # procedure is on "water the seeds" is the photo step done early: watering
    # was passed over. One mistake, one alert -- a skip, not a wrong object too.
    r = Replay("seed_germination.yaml")
    pot(r, 8, touch=True)
    r.scene(40)  # a few seconds into "water the seeds"
    held(r, 10, "cell phone")
    assert r.done("s3")
    assert r.state("s2") == StepState.SKIPPED
    assert r.alerts == [("skip", "s2")]


def test_strict_mode_flags_returning_the_tray_before_the_photo() -> None:
    r = Replay("seed_germination.yaml", "strict")
    pot(r, 8, touch=True)
    for _ in range(8):
        r.scene(1, at=HAND, seen=("bottle", "potted plant"), touch=True)
    assert r.done("s2")
    pot(r, 45, at=CHAMBER)  # back in the chamber, no photograph taken
    assert r.state("s4") == StepState.OUT_OF_ORDER
    assert ("out_of_order", "s4") in r.alerts


def test_a_bowl_of_sprouts_is_a_seed_tray_too() -> None:
    r = Replay("seed_germination.yaml")
    r.scene(8, at=BENCH, seen=("bowl",), touch=True)
    assert r.done("s1")
    for _ in range(8):
        r.scene(1, at=HAND, seen=("bottle", "bowl"), touch=True)
    assert r.done("s2")
