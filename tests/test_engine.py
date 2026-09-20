"""Step state machine mechanics."""

from __future__ import annotations

from orbital_har.core.types import EventType, StepState
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure
from orbital_har.simkit.scenario import MARKER_R, MARKER_Y, standard_world
from tests.conftest import run_events


def stage_first_two_steps(sc):
    """Drive PROC-A through s1 and s2."""
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.7)
    return sc


def test_steps_activate_in_order(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    engine, _ = run_events(proc_a, sc.events)

    assert engine.state_of("s1") is StepState.COMPLETE
    assert engine.state_of("s2") is StepState.ACTIVE
    assert engine.state_of("s3") is StepState.PENDING


def test_first_step_is_active_immediately(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.2)
    engine, _ = run_events(proc_a, sc.events)
    assert engine.state_of("s1") is StepState.ACTIVE
    assert engine.next_step is not None
    assert engine.next_step.step.id == "s1"


def test_activation_carries_the_voice_prompt(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.2)
    engine = Engine(proc_a)
    emitted = []
    for event in sc.events:
        emitted.extend(engine.on_event(event))

    activations = [
        e
        for e in emitted
        if e.type == EventType.STEP_STATE.value and e.payload["state"] == StepState.ACTIVE.value
    ]
    assert activations
    assert activations[0].payload["voice"] == "Open the outer container."


def test_latched_contact_survives_release(proc_a: Procedure) -> None:
    """s2 needs contact AND placement, but the hand is long gone by then.

    Without latching the step could never complete -- this is the regression
    guard for that.
    """
    sc = stage_first_two_steps(standard_world())
    engine, _ = run_events(proc_a, sc.events)
    assert engine.state_of("s2") is StepState.COMPLETE


def test_skip_marks_the_passed_over_step(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.5)
    # s3 never happens; jump straight to staging the yellow box AND the vial.
    sc.set_class("red_box", "red_box_open").hold(1.0)

    engine, alerts = run_events(proc_a, sc.events)
    assert engine.state_of("s3") is StepState.SKIPPED
    assert engine.state_of("s4") is StepState.COMPLETE
    assert ("skip", "s3") in alerts


def test_skip_does_not_also_raise_out_of_order(proc_a: Procedure) -> None:
    """One mistake, one alert. Noise is how an assistant gets muted."""
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.5)
    sc.set_class("red_box", "red_box_open").hold(1.0)

    _, alerts = run_events(proc_a, sc.events)
    kinds = {kind for kind, _ in alerts}
    assert "skip" in kinds
    assert "out_of_order" not in kinds


def test_strict_mode_refuses_to_complete_out_of_order(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.5)
    sc.set_class("red_box", "red_box_open").hold(1.0)

    engine, alerts = run_events(proc_a, sc.events, EngineConfig(strict_preconditions=True))
    assert engine.state_of("s4") is StepState.OUT_OF_ORDER
    assert ("out_of_order", "s4") in alerts


def test_crew_always_has_a_next_instruction_after_a_skip(proc_a: Procedure) -> None:
    """A skipped step does not satisfy a precondition, so the next step would
    otherwise stay blocked forever. An advisor with nothing to say is useless.
    """
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.5)
    sc.set_class("red_box", "red_box_open").hold(1.0)

    engine, _ = run_events(proc_a, sc.events)
    assert any(rt.state is StepState.ACTIVE for rt in engine.runtimes)


def test_override_advances_the_procedure(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.5)
    engine = Engine(proc_a)
    for event in sc.events:
        engine.on_event(event)

    emitted = engine.override("s1", t=sc.now, actor="crew")
    assert engine.state_of("s1") is StepState.OVERRIDDEN
    assert engine.state_of("s2") is StepState.ACTIVE
    assert any(e.payload.get("state") == "overridden" for e in emitted)


def test_override_satisfies_downstream_preconditions(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.5)
    engine = Engine(proc_a)
    for event in sc.events:
        engine.on_event(event)
    engine.override("s1", t=sc.now)

    sc2 = standard_world()
    sc2.hold(0.1)
    assert engine.state_of("s2") is StepState.ACTIVE


def test_unverified_when_evidence_is_too_weak(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.set_class("outer_box", "outer_box_open")
    sc.set_conf("outer_box", 0.40)
    sc.hold(3.0)

    engine, alerts = run_events(proc_a, sc.events)
    assert engine.state_of("s1") is StepState.UNVERIFIED
    assert ("unverified", "s1") in alerts


def test_middling_confidence_holds_without_a_verdict(proc_a: Procedure) -> None:
    """Between the two thresholds the engine waits. It does not guess either way."""
    sc = standard_world()
    sc.set_class("outer_box", "outer_box_open")
    sc.set_conf("outer_box", 0.65)
    sc.hold(3.0)

    engine, alerts = run_events(proc_a, sc.events)
    assert engine.state_of("s1") is StepState.ACTIVE
    assert alerts == []


def test_group_members_do_not_skip_each_other(proc_b: Procedure) -> None:
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)
    sc.grab("hand", "yellow_box").hold(0.4)
    sc.place("yellow_box", MARKER_Y).hold(0.4)
    sc.release("hand", "yellow_box").hold(0.5)
    sc.grab("hand", "red_box").hold(0.4)
    sc.place("red_box", MARKER_R).hold(0.4)
    sc.release("hand", "red_box").hold(0.5)
    # s5 before s4 -- both are in group g1, so neither may accuse the other.
    sc.set_class("red_box", "red_box_open").hold(0.8)
    sc.set_class("yellow_box", "yellow_box_open").hold(0.8)

    engine, alerts = run_events(proc_b, sc.events)
    assert engine.state_of("s4") is StepState.COMPLETE
    assert engine.state_of("s5") is StepState.COMPLETE
    assert [k for k, _ in alerts] == []


def test_summary_counts_states(proc_a: Procedure) -> None:
    sc = stage_first_two_steps(standard_world())
    engine, _ = run_events(proc_a, sc.events)
    summary = engine.summary()
    assert summary["steps_total"] == 6
    assert summary["counts"]["complete"] == 2
    assert summary["complete"] is False
