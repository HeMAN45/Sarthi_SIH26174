"""Wrong-object alert: picking up what only a later step needs.

"Pick up the bottle" -- and the operator picks up the phone. The completion
lookahead cannot see that (on purpose: resting state must not complete a
far-future step), but a hand on an object is not resting state.
"""

from __future__ import annotations

from typing import Any

from orbital_har.core.types import AlertKind, Event, EventType, StepState
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.experiments import compose

BOX = [100.0, 100.0, 300.0, 400.0]


class Rig:
    """Feeds frames to an engine, 10 frames a second, and keeps what it says."""

    def __init__(self, proc: Procedure, config: EngineConfig | None = None) -> None:
        self.engine = Engine(proc, config or EngineConfig(completion_lookahead=0))
        self.t = 0.0
        self.fid = 0
        self.seq = 0
        self.out: list[Event] = []

    def _send(self, type_: str, payload: dict[str, Any]) -> None:
        self.seq += 1
        self.out += self.engine.on_event(Event(self.t, self.seq, "test", type_, payload))

    def idle(self, seconds: float = 3.0) -> Rig:
        """Nothing handled for a while -- the operator settling in."""
        return self.show(frames=round(seconds * 10))

    def show(self, *classes: str, touching: tuple[str, ...] = (), frames: int = 1) -> Rig:
        for _ in range(frames):
            self.fid += 1
            self.t += 0.1
            self._send(EventType.FRAME.value, {"frame_id": self.fid, "w": 640, "h": 480})
            objects = [{"cls": c, "conf": 0.9, "bbox": BOX} for c in classes]
            self._send(EventType.DETECTION.value, {"frame_id": self.fid, "objects": objects})
            if touching:
                pairs = [{"a": "hand", "cls": c, "conf": 0.9} for c in touching]
                self._send(EventType.CONTACT.value, {"frame_id": self.fid, "pairs": pairs})
        return self

    @property
    def wrong(self) -> list[dict[str, Any]]:
        return [
            e.payload
            for e in self.out
            if e.type == EventType.ALERT.value and e.payload["kind"] == AlertKind.WRONG_OBJECT.value
        ]

    @property
    def alerts(self) -> list[str]:
        return [e.payload["kind"] for e in self.out if e.type == EventType.ALERT.value]


def three_objects() -> Procedure:
    return compose([{"object": "bottle"}, {"object": "cell phone"}, {"object": "book"}], "Three")


def test_picking_up_a_later_object_alerts_by_voice_line() -> None:
    rig = Rig(three_objects()).idle().show("cell phone", frames=10)
    assert len(rig.wrong) == 1
    alert = rig.wrong[0]
    assert alert["step_id"] == "s2" and alert["expected_step_id"] == "s1"
    assert alert["message"] == "Wrong object: the cell phone is for step 2. Now: Present the bottle"
    # Advisory: nothing was completed or skipped on the strength of it.
    assert rig.engine.state_of("s1") is StepState.ACTIVE
    assert rig.engine.state_of("s2") is StepState.PENDING


def test_a_glimpse_is_not_a_mistake() -> None:
    assert Rig(three_objects()).idle().show("cell phone", frames=4).wrong == []


def test_one_alert_per_object_until_the_cooldown_passes() -> None:
    rig = Rig(three_objects()).idle().show("cell phone", frames=60)  # 6 s of it
    assert len(rig.wrong) == 1
    rig.show("cell phone", frames=50)  # past the 10 s cooldown
    assert len(rig.wrong) == 2


def test_an_earlier_steps_object_is_still_allowed() -> None:
    rig = Rig(three_objects()).show("bottle", frames=10)
    assert rig.engine.state_of("s1") is StepState.COMPLETE
    rig.show("bottle", frames=20)  # still holding it after step one
    assert rig.wrong == []


def test_the_right_object_in_the_other_hand_is_not_called() -> None:
    rig = Rig(three_objects()).idle().show("bottle", "cell phone", frames=10)
    assert rig.wrong == []
    assert rig.engine.state_of("s1") is StepState.COMPLETE


def test_strict_mode_leaves_the_next_step_to_the_out_of_order_verdict() -> None:
    strict = EngineConfig(completion_lookahead=1, strict_preconditions=True)
    rig = Rig(three_objects(), strict).idle().show("cell phone", frames=12)
    assert rig.wrong == []  # one mistake, one alert
    assert AlertKind.OUT_OF_ORDER.value in rig.alerts

    rig = Rig(three_objects(), strict).idle().show("book", frames=10)  # past the lookahead
    assert [a["step_id"] for a in rig.wrong] == ["s3"]


def test_body_action_steps_are_covered_too() -> None:
    proc = compose([{"gesture": "hand_raised"}, {"object": "bottle"}], "Wave then bottle")
    rig = Rig(proc).idle().show("bottle", frames=10)
    assert rig.wrong[0]["message"] == "Wrong object: the bottle is for step 2. Now: Raise your hand"


def scene() -> Procedure:
    return Procedure.model_validate(
        {
            "procedure": {"id": "scene", "name": "Scene"},
            "objects": [
                {"id": "bottle", "classes": ["bottle"]},
                {"id": "phone", "classes": ["cell phone"]},
            ],
            "steps": [
                {
                    "id": "s1",
                    "name": "Pick up the bottle",
                    "voice": "Pick up the bottle.",
                    "requires": [{"contact": ["hand", "bottle"], "hold_frames": 3}],
                },
                {
                    "id": "s2",
                    "name": "Pick up the phone",
                    "voice": "Pick up the phone.",
                    "preconditions": ["s1"],
                    "requires": [{"contact": ["hand", "phone"], "hold_frames": 3}],
                },
            ],
        }
    )


def test_in_a_scene_only_a_hand_on_it_counts() -> None:
    # Both objects sit on the table in plain view: that is the scene, not a mistake.
    rig = Rig(scene()).show("bottle", "cell phone", frames=40)
    assert rig.wrong == []
    rig.show("bottle", "cell phone", touching=("cell phone",), frames=8)
    assert (
        rig.wrong[0]["message"] == "Wrong object: the phone is for step 2. Now: Pick up the bottle"
    )


def test_can_be_switched_off() -> None:
    quiet = EngineConfig(completion_lookahead=0, wrong_object_alerts=False)
    assert Rig(three_objects(), quiet).idle().show("cell phone", frames=20).wrong == []


def test_what_was_in_view_when_the_step_began_is_the_scene_not_a_mistake() -> None:
    # The bottle standing at home when the run starts is the last step's state,
    # not the operator jumping ahead.
    proc = compose([{"object": "cup"}, {"object": "bottle"}], "Cup then bottle")
    rig = Rig(proc).show("bottle", frames=40)
    assert rig.wrong == []
    # Taken away and brought back, it is an action -- and a wrong one.
    rig.idle(1.0).show("bottle", frames=10)
    assert [a["step_id"] for a in rig.wrong] == ["s2"]


def _water(config: EngineConfig | None = None) -> tuple[Rig, Any]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "procedures"
    rig = Rig(Procedure.load(root / "water_sample.yaml"), config)

    def gesture(name: str, frames: int = 8) -> None:
        for _ in range(frames):
            rig.show()
            rig._send(
                EventType.GESTURE.value,
                {"frame_id": rig.fid, "gestures": [{"name": name, "side": "left", "conf": 0.9}]},
            )

    return rig, gesture


def test_the_water_sampling_demo_runs_and_catches_the_wrong_object() -> None:
    rig, gesture = _water()
    gesture("hand_raised")
    assert rig.engine.state_of("s1") is StepState.COMPLETE
    rig.idle().show("cell phone", frames=8)  # the tablet, when the container is asked for
    assert rig.wrong[0]["message"] == (
        "Wrong object: the crew tablet is for step 4. Now: Retrieve the sample container"
    )
    rig.idle(1.0).show("bottle", frames=8)
    rig.show("bottle", "cup", frames=8)  # two objects at once
    rig.show("cell phone", frames=8)
    gesture("both_hands_raised")
    assert rig.engine.is_complete


def test_strict_mode_flags_logging_a_sample_never_collected() -> None:
    rig, gesture = _water(EngineConfig(completion_lookahead=1, strict_preconditions=True))
    gesture("hand_raised")
    rig.idle(1.0).show("bottle", frames=8)
    rig.idle(1.0).show("cell phone", frames=8)  # log it before collecting it
    assert rig.engine.state_of("s4") is StepState.OUT_OF_ORDER
    assert AlertKind.OUT_OF_ORDER.value in rig.alerts
