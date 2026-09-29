"""The live engine configuration: holds as durations, one step of lookahead,
and evidence that is not inherited from the step before.

These are the settings ``make_engine`` gives every run from the console. The
golden corpus is replayed through them too, because a regression suite that
only exercises the engine's class defaults proves nothing about what ships.
"""

from __future__ import annotations

import pytest

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.reasoning.schema import Procedure
from orbital_har.reasoning.window import FrameSnapshot, Window
from orbital_har.runtime.session import HOLD_FPS, make_engine
from orbital_har.simkit.fixtures import ALL_FIXTURES, build
from tests.conftest import PROCEDURES

HOME = (460, 300, 520, 440)  # inside drink_water's home rect, 640x480
AWAY = (250, 120, 310, 260)


def _proc(id_: str, *steps: dict, objects: tuple[dict, ...] = ()) -> Procedure:
    """An inline procedure: steps are ``id``, ``requires`` and optional ``preconditions``."""
    return Procedure.model_validate(
        {
            "procedure": {"id": id_, "name": id_, "version": 1},
            "objects": list(objects),
            "steps": [{"name": s["id"], "voice": s["id"], **s} for s in steps],
        }
    )


def _window(fps: float, frames: int) -> Window:
    w = Window()
    for i in range(frames):
        w.append(FrameSnapshot(frame_id=i, t=i / fps))
    return w


class Feed:
    """Frames of boxes, hand contact and gestures at any frame rate."""

    def __init__(self, proc: Procedure, fps: float, mode: str = "clean") -> None:
        self.engine = make_engine(proc, mode)
        self.fps = fps
        self.fid = 0
        self.alerts: list[tuple[str, str | None]] = []

    def frames(
        self,
        n: int,
        *,
        at: tuple[int, int, int, int] | None = None,
        seen: tuple[str, ...] = ("bottle",),
        touch: bool = False,
        gestures: tuple[str, ...] = (),
    ) -> None:
        for _ in range(n):
            self.fid += 1
            t = self.fid / self.fps
            objs = [] if at is None else [{"cls": c, "conf": 0.9, "bbox": list(at)} for c in seen]
            events = [
                (EventType.FRAME.value, {"frame_id": self.fid, "w": 640, "h": 480}),
                (EventType.DETECTION.value, {"frame_id": self.fid, "objects": objs}),
            ]
            if touch and at is not None:
                pairs = [{"a": "hand", "side": "right", "cls": c, "conf": 0.9} for c in seen]
                events.append((EventType.CONTACT.value, {"frame_id": self.fid, "pairs": pairs}))
            if gestures:
                body = [{"name": g, "side": "both", "conf": 0.9} for g in gestures]
                events.append((EventType.GESTURE.value, {"frame_id": self.fid, "gestures": body}))
            for i, (type_, payload) in enumerate(events):
                event = Event(t, self.fid * 10 + i, "test", type_, payload)
                for out in self.engine.on_event(event):
                    if out.type == EventType.ALERT.value:
                        self.alerts.append((out.payload["kind"], out.payload.get("step_id")))

    def done(self, step_id: str) -> bool:
        return self.engine.state_of(step_id) == StepState.COMPLETE


# ------------------------------------------------------------ Window.streak


def test_without_a_rate_a_hold_counts_frames() -> None:
    w = _window(fps=4, frames=12)
    assert len(w.streak(12)) == 12
    assert w.streak(13) is None


def test_at_the_reference_rate_a_duration_is_the_same_frames() -> None:
    w = _window(fps=HOLD_FPS, frames=40)
    assert w.streak(12, fps=HOLD_FPS) == w.streak(12)


def test_at_a_slow_rate_a_duration_needs_fewer_frames() -> None:
    # Twelve frames written for 15 FPS span 0.73 s. At 4 FPS that is four
    # frames, not the three seconds twelve frames would take.
    frames = _window(fps=4, frames=20).streak(12, fps=HOLD_FPS)
    assert len(frames) == 4
    assert frames[-1].t - frames[0].t >= 11 / HOLD_FPS


def test_explicit_seconds_win_and_short_history_waits() -> None:
    w = _window(fps=10, frames=15)
    assert len(w.streak(12, fps=HOLD_FPS, seconds=0.5)) == 6
    assert w.streak(12, fps=HOLD_FPS, seconds=5.0) is None
    assert Window().streak(3, fps=HOLD_FPS) is None


def test_a_procedure_may_state_its_hold_in_seconds() -> None:
    proc = _proc(
        "p",
        {"id": "s1", "requires": [{"detect": "cup", "hold_s": 1.5}]},
        objects=({"id": "cup", "classes": ["cup"]},),
    )
    assert proc.steps[0].requires[0].hold_s == 1.5


# ------------------------------------------------------- holds live, at 4 FPS


def test_a_slow_machine_does_not_make_the_operator_freeze() -> None:
    # Each phase feeds one frame more than the hold: the engine judges a frame
    # once the next one begins.
    f = Feed(Procedure.load(PROCEDURES / "drink_water.yaml"), fps=4)
    f.frames(8, at=HOME, seen=("bottle", "bottle_closed"))
    f.frames(3, at=HOME, seen=("bottle", "bottle_closed"), touch=True)
    assert f.done("s1")
    f.frames(4, at=AWAY, seen=("bottle", "bottle_open"), touch=True)
    assert f.done("s2")
    # A sip: 0.73 s of hand at the face is four frames here. Counted in frames
    # it was twelve -- three seconds with the hand held still at the mouth.
    f.frames(5, at=AWAY, seen=("bottle", "bottle_open"), gestures=("hand_to_face",))
    assert f.done("s3")
    assert f.alerts == []


# --------------------------------------------------------- skips, live modes


def test_clean_mode_hears_a_skipped_step() -> None:
    f = Feed(Procedure.load(PROCEDURES / "drink_water.yaml"), fps=15)
    f.frames(8, at=HOME, seen=("bottle", "bottle_closed"), touch=True)
    assert f.done("s1")
    # Drinks straight from the closed bottle: "open the cap" never happened.
    f.frames(16, at=AWAY, seen=("bottle", "bottle_closed"), gestures=("hand_to_face",))
    assert f.done("s3")
    assert f.engine.state_of("s2") == StepState.SKIPPED
    assert f.alerts == [("skip", "s2")]


_UNREACHABLE = pytest.mark.xfail(
    strict=True,
    reason="live tau_abstain (0.35) equals DETECTION_FLOOR, so satisfied evidence is "
    "never below it and UNVERIFIED cannot happen in a live run (invariant #12)",
)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, marks=_UNREACHABLE) if n == "proc_a_unverified_s4" else n
        for n in sorted(ALL_FIXTURES)
    ],
)
def test_the_golden_corpus_holds_under_the_live_clean_mode(name: str) -> None:
    fixture = build(name)
    proc = Procedure.load(PROCEDURES / f"{fixture.procedure}.yaml")
    engine = make_engine(proc, "clean")
    alerts = []
    for event in fixture.scenario.events:
        alerts += [o for o in engine.on_event(event) if o.type == EventType.ALERT.value]
    alerts += [o for o in engine.flush() if o.type == EventType.ALERT.value]

    states = {sid: s.value for sid, s in engine.states.items()}
    for step_id, expected in fixture.expected.final_states.items():
        assert states[step_id] == expected, f"{name}: {step_id} is {states[step_id]}"
    assert [(a.payload["kind"], a.payload["step_id"]) for a in alerts] == [
        tuple(a) for a in fixture.expected.alerts
    ]


def test_strict_mode_holds_the_early_step_out_of_sequence() -> None:
    fixture = build("proc_a_skip_s4")
    engine = make_engine(Procedure.load(PROCEDURES / "proc_a.yaml"), "strict")
    alerts = []
    for event in fixture.scenario.events:
        alerts += [o for o in engine.on_event(event) if o.type == EventType.ALERT.value]
    assert engine.state_of("s5") == StepState.OUT_OF_ORDER
    assert [(a.payload["kind"], a.payload["step_id"]) for a in alerts] == [("out_of_order", "s5")]


# ------------------------------------------------------ inherited evidence


def _handshake_procedure(close_hold: int) -> Procedure:
    """Open and close are the same gesture; the drink between them is not."""
    return _proc(
        "hs",
        {"id": "s1", "requires": [{"gesture": "hands_together", "hold_frames": 10}]},
        {
            "id": "s2",
            "preconditions": ["s1"],
            "requires": [{"gesture": "hand_to_face", "hold_frames": 12}],
        },
        {
            "id": "s3",
            "preconditions": ["s2"],
            "requires": [{"gesture": "hands_together", "hold_frames": close_hold}],
        },
    )


@pytest.mark.parametrize("close_hold", [10, 20])
def test_a_step_does_not_complete_on_evidence_it_inherited(close_hold: int) -> None:
    # The hands stay together long after "open" completes. "Close" comes up for
    # judgement with that evidence already in view, so it must not fire on it --
    # not even when its hold is longer and could otherwise wait it out.
    f = Feed(_handshake_procedure(close_hold), fps=15)
    f.frames(40, gestures=("hands_together",))
    assert f.done("s1")
    assert not f.done("s3")
    assert f.engine.state_of("s2") == StepState.ACTIVE
    f.frames(14, gestures=("hand_to_face",))
    assert f.done("s2")
    f.frames(close_hold + 2, gestures=("hands_together",))
    assert f.done("s3")
    assert f.alerts == []
