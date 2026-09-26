"""Which hand, and what the hand does with the object: hold, pour, move.

Pure reasoning tests: scripted frames straight into the engine, as a live run
would emit them, no camera and no model.
"""

from __future__ import annotations

from typing import Any

import pytest

from orbital_har.core.types import AlertKind, Event, EventType, StepState
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.predicates import EvalContext, evaluate
from orbital_har.reasoning.schema import Procedure
from orbital_har.reasoning.window import FrameSnapshot, Window
from orbital_har.runtime.experiments import compose
from orbital_har.runtime.session import perception_needs, voice_lines

UPRIGHT = (300.0, 100.0, 340.0, 220.0)  # a standing bottle: three times taller than wide
TIPPED = (280.0, 140.0, 400.0, 220.0)  # the same bottle poured: wider than tall


class Rig:
    """Ten frames a second of boxes, hand contacts and gestures, into an engine."""

    def __init__(self, proc: Procedure, config: EngineConfig | None = None) -> None:
        self.engine = Engine(proc, config or EngineConfig(completion_lookahead=0))
        self.t, self.fid, self.seq = 0.0, 0, 0
        self.out: list[Event] = []

    def _send(self, type_: str, payload: dict[str, Any]) -> None:
        self.seq += 1
        self.out += self.engine.on_event(Event(self.t, self.seq, "test", type_, payload))

    def frames(
        self,
        n: int,
        *,
        boxes: dict[str, tuple[float, float, float, float]] | None = None,
        touch: dict[str, str] | None = None,
        gestures: dict[str, str] | list[tuple[str, str]] | None = None,
    ) -> Rig:
        """``touch``: class -> hand side. ``gestures``: name -> side, or pairs."""
        for _ in range(n):
            self.fid += 1
            self.t += 0.1
            self._send(EventType.FRAME.value, {"frame_id": self.fid, "w": 640, "h": 480})
            objs = [{"cls": c, "conf": 0.9, "bbox": list(b)} for c, b in (boxes or {}).items()]
            self._send(EventType.DETECTION.value, {"frame_id": self.fid, "objects": objs})
            if touch:
                pairs = [{"a": "hand", "cls": c, "side": s, "conf": 0.9} for c, s in touch.items()]
                self._send(EventType.CONTACT.value, {"frame_id": self.fid, "pairs": pairs})
            if gestures:
                pairs_ = gestures.items() if isinstance(gestures, dict) else gestures
                gs = [{"name": g, "side": s, "conf": 0.9} for g, s in pairs_]
                self._send(EventType.GESTURE.value, {"frame_id": self.fid, "gestures": gs})
        return self

    def state(self, step: str) -> StepState:
        return self.engine.state_of(step)

    def alerts(self, kind: AlertKind) -> list[dict[str, Any]]:
        return [
            e.payload
            for e in self.out
            if e.type == EventType.ALERT.value and e.payload["kind"] == kind.value
        ]


# ------------------------------------------------------------ holding by hand


def test_hold_in_the_right_hand_needs_the_right_hand() -> None:
    proc = compose([{"object": "bottle", "how": "hold", "hand": "right"}], "Right hand")
    rig = Rig(proc).frames(10, boxes={"bottle": UPRIGHT}, touch={"bottle": "left"})
    assert rig.state("s1") != StepState.COMPLETE
    rig.frames(6, boxes={"bottle": UPRIGHT}, touch={"bottle": "right"})
    assert rig.state("s1") == StepState.COMPLETE


# -------------------------------------------------------------------- pouring


def test_pouring_is_the_bottle_in_hand_and_tipped_over() -> None:
    proc = compose([{"object": "bottle", "how": "pour"}], "Pour")
    rig = Rig(proc).frames(10, boxes={"bottle": UPRIGHT}, touch={"bottle": "right"})
    assert rig.state("s1") != StepState.COMPLETE  # holding it upright is not pouring
    rig.frames(6, boxes={"bottle": TIPPED}, touch={"bottle": "right"})
    assert rig.state("s1") == StepState.COMPLETE


def test_a_bottle_lying_on_the_table_is_not_being_poured() -> None:
    proc = compose([{"object": "bottle", "how": "pour"}], "Pour")
    rig = Rig(proc).frames(20, boxes={"bottle": TIPPED})  # tipped, but no hand on it
    assert rig.state("s1") != StepState.COMPLETE


# --------------------------------------------------------------------- moving


def test_moving_is_carrying_it_across_the_picture() -> None:
    proc = compose([{"object": "cup", "how": "move"}], "Carry")
    rig = Rig(proc).frames(5, boxes={"cup": (50, 300, 110, 380)}, touch={"cup": "left"})
    assert rig.state("s1") != StepState.COMPLETE
    for x in range(60, 400, 40):  # carried to the other side, still in hand
        rig.frames(1, boxes={"cup": (x, 300, x + 60, 380)}, touch={"cup": "left"})
    assert rig.state("s1") == StepState.COMPLETE


def test_a_nudge_is_not_a_move() -> None:
    proc = compose([{"object": "cup", "how": "move"}], "Carry")
    rig = Rig(proc)
    for x in (50, 60, 70, 60, 50, 60, 70):
        rig.frames(2, boxes={"cup": (x, 300, x + 60, 380)}, touch={"cup": "left"})
    assert rig.state("s1") != StepState.COMPLETE


def test_a_picture_move_needs_no_rack_but_a_millimetre_move_does() -> None:
    proc = compose([{"object": "cup", "how": "move"}], "Carry")
    assert perception_needs(proc) == (False, True)


# ------------------------------------------------------------ held up close


def test_show_steps_keep_their_floor_in_a_scene() -> None:
    proc = compose([{"object": "cup", "how": "hold"}, {"object": "bottle"}], "Floor")
    rig = Rig(proc).frames(6, boxes={"cup": UPRIGHT}, touch={"cup": "right"})
    assert rig.state("s1") == StepState.COMPLETE
    rig.frames(12, boxes={"bottle": (10, 10, 40, 60)})  # at the back of the desk: 0.5 %
    assert rig.state("s2") != StepState.COMPLETE
    rig.frames(10, boxes={"bottle": (200, 50, 380, 400)})  # held up: 20 %
    assert rig.state("s2") == StepState.COMPLETE


# ------------------------------------------------------------------ wrong hand


def left_hand_steps() -> Procedure:
    return compose(
        [
            {"gesture": "hand_raised", "hand": "right"},
            {"gesture": "hand_raised", "hand": "left"},
            {"object": "bottle", "how": "hold", "hand": "left"},
        ],
        "Hands",
    )


def test_raising_the_other_hand_alerts() -> None:
    rig = Rig(left_hand_steps()).frames(10, gestures={"hand_raised": "right"})
    assert rig.state("s1") == StepState.COMPLETE
    rig.frames(30)  # hands down; step two begins
    rig.frames(8, gestures={"hand_raised": "right"})  # the wrong one again
    (alert,) = rig.alerts(AlertKind.WRONG_HAND)
    assert alert["message"] == "Wrong hand: use your left hand. Now: Raise your left hand"
    assert alert["step_id"] == "s2"
    assert rig.state("s2") == StepState.ACTIVE  # advisory: nothing completed


def test_the_last_steps_hand_still_up_is_not_a_mistake() -> None:
    rig = Rig(left_hand_steps()).frames(30, gestures={"hand_raised": "right"})
    assert rig.state("s1") == StepState.COMPLETE
    assert rig.alerts(AlertKind.WRONG_HAND) == []


def test_holding_in_the_wrong_hand_alerts_and_the_right_hand_clears_it() -> None:
    rig = Rig(left_hand_steps()).frames(10, gestures={"hand_raised": "right"})
    rig.frames(20).frames(10, gestures={"hand_raised": "left"})
    assert rig.state("s2") == StepState.COMPLETE
    rig.frames(20).frames(8, boxes={"bottle": UPRIGHT}, touch={"bottle": "right"})
    assert [a["step_id"] for a in rig.alerts(AlertKind.WRONG_HAND)] == ["s3"]
    rig.frames(6, boxes={"bottle": UPRIGHT}, touch={"bottle": "left"})
    assert rig.state("s3") == StepState.COMPLETE


def test_one_wrong_hand_alert_per_step_until_the_cooldown_passes() -> None:
    rig = Rig(left_hand_steps()).frames(8, gestures={"hand_raised": "right"}).frames(30)
    rig.frames(60, gestures={"hand_raised": "right"})  # six seconds of the wrong hand
    assert len(rig.alerts(AlertKind.WRONG_HAND)) == 1


def test_both_hands_up_is_not_the_wrong_hand() -> None:
    rig = Rig(left_hand_steps()).frames(10, gestures={"hand_raised": "right"}).frames(30)
    both = [("hand_raised", "right"), ("hand_raised", "left"), ("both_hands_raised", "both")]
    rig.frames(10, gestures=both)  # the left hand is up too
    assert rig.alerts(AlertKind.WRONG_HAND) == []
    assert rig.state("s2") == StepState.COMPLETE


def test_wrong_hand_lines_are_prerendered() -> None:
    assert "Wrong hand: use your left hand. Now: Raise your left hand" in voice_lines(
        left_hand_steps()
    )


def test_can_be_switched_off() -> None:
    quiet = EngineConfig(completion_lookahead=0, wrong_hand_alerts=False)
    rig = Rig(left_hand_steps(), quiet).frames(8, gestures={"hand_raised": "right"}).frames(30)
    rig.frames(20, gestures={"hand_raised": "right"})
    assert rig.alerts(AlertKind.WRONG_HAND) == []


# ------------------------------------------------------------- the predicate


def _window(*boxes) -> Window:
    w = Window()
    for i, b in enumerate(boxes):
        snap = FrameSnapshot(frame_id=i, t=i * 0.1, width=640, height=480)
        from orbital_har.core.types import DetectedObject

        snap.objects.append(DetectedObject("bottle", 0.9, b))
        w.append(snap)
    return w


@pytest.mark.parametrize(("box", "tipped"), [(UPRIGHT, False), (TIPPED, True)])
def test_tilted_reads_the_box_shape(box, tipped) -> None:
    proc = compose([{"object": "bottle", "how": "pour"}], "Pour")
    tilted = proc.steps[0].requires[1]
    result = evaluate(tilted, EvalContext(window=_window(*[box] * 4), procedure=proc))
    assert result.satisfied is tipped


def test_moved_needs_exactly_one_measure() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        Procedure.model_validate(
            {
                "procedure": {"id": "m", "name": "M"},
                "objects": [{"id": "cup", "classes": ["cup"]}],
                "steps": [{"id": "s1", "name": "M", "voice": "M.", "requires": [{"moved": "cup"}]}],
            }
        )


# ------------------------------------------------------- the fitness demo


def test_the_fitness_check_runs_end_to_end_and_catches_the_wrong_hand() -> None:
    from pathlib import Path

    from orbital_har.runtime.session import make_engine

    proc = Procedure.load(Path(__file__).resolve().parents[1] / "procedures" / "crew_fitness.yaml")
    rig = Rig(proc)
    rig.engine = make_engine(proc, "clean")  # the live run's configuration
    rig.frames(30)  # settling in
    rig.frames(8, gestures={"hand_raised": "left"})  # the wrong hand
    assert [a["step_id"] for a in rig.alerts(AlertKind.WRONG_HAND)] == ["s1"]
    rig.frames(10).frames(8, gestures={"hand_raised": "right"})
    rig.frames(8, gestures={"arms_crossed": "both"})
    rig.frames(4, gestures={"waving": "left"})
    bottle = {"bottle": UPRIGHT}
    rig.frames(5, boxes=bottle, touch={"bottle": "right"}, gestures={"lifting": "right"})
    rig.frames(8, boxes=bottle, gestures={"hand_to_face": "right"})
    rig.frames(8, gestures={"both_hands_raised": "both"})
    assert rig.engine.is_complete
    assert [
        s for s in ("s1", "s2", "s3", "s4", "s5", "s6") if rig.state(s) != StepState.COMPLETE
    ] == []
