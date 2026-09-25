"""Step state machine mechanics."""

from __future__ import annotations

from orbital_har.core.types import Event, EventType, StepState
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


# --------------------------------------------------------------------------
# Crew skip
# --------------------------------------------------------------------------


def test_skip_targets_the_active_step_by_default(proc_a: Procedure) -> None:
    """A crew skip with no step id means "skip what you are asking me to do"."""
    sc = standard_world()
    sc.hold(0.3)
    engine, _ = run_events(proc_a, sc.events)
    assert engine.state_of("s1") is StepState.ACTIVE

    emitted = engine.skip(None, t=sc.now)

    assert engine.state_of("s1") is StepState.SKIPPED
    kinds = [e.payload["kind"] for e in emitted if e.type == EventType.ALERT.value]
    assert kinds == ["skip"]


def test_skip_records_its_reason_and_arms_the_next_step(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.3)
    engine, _ = run_events(proc_a, sc.events)

    engine.skip(None, t=sc.now, reason="object unavailable")

    rt = engine.runtimes[0]
    assert rt.reason == "object unavailable"
    assert rt.resolved_t is not None
    # A skipped step does not satisfy a precondition, so s2 must still be
    # promoted explicitly -- the crew always has a next instruction.
    assert engine.state_of("s2") is StepState.ACTIVE


def test_skip_is_idempotent_on_a_resolved_step(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.3)
    engine, _ = run_events(proc_a, sc.events)
    engine.skip("s1", t=sc.now)

    assert engine.skip("s1", t=sc.now + 1.0) == []


def test_skip_via_crew_action_event(proc_a: Procedure) -> None:
    """The bus path and the direct call must agree."""
    sc = standard_world()
    sc.hold(0.3)
    engine, _ = run_events(proc_a, sc.events)

    event = Event(
        t=sc.now,
        seq=9999,
        src="ui",
        type=EventType.CREW_ACTION.value,
        payload={"action": "skip", "step_id": "s1", "reason": "crew skipped"},
    )
    emitted = engine.on_event(event)

    assert engine.state_of("s1") is StepState.SKIPPED
    assert any(e.payload.get("kind") == "skip" for e in emitted)


def test_skip_unknown_step_is_a_no_op(proc_a: Procedure) -> None:
    sc = standard_world()
    sc.hold(0.3)
    engine, _ = run_events(proc_a, sc.events)
    assert engine.skip("nope", t=sc.now) == []


# --------------------------------------------------------------------------
# Free-float advisory (D-07)
# --------------------------------------------------------------------------


def _drift(sc, obj_id: str, steps: int = 25, mm_per_frame: float = 20.0):
    """Move an object steadily, one nudge per frame."""
    x, y, z = sc.objects[obj_id].pos
    for i in range(1, steps + 1):
        sc.place(obj_id, (x + i * mm_per_frame, y, z))
        sc.hold(1 / 30)
    return sc


def test_free_float_advisory_when_a_tethered_object_drifts_untouched(
    proc_a: Procedure,
) -> None:
    sc = standard_world()
    sc.hold(0.5)
    _drift(sc, "sample_vial")

    _, alerts = run_events(proc_a, sc.events)
    assert "free_float" in [kind for kind, _ in alerts]


def test_no_free_float_advisory_while_the_object_is_held(proc_a: Procedure) -> None:
    """Held is not adrift -- that is the whole point of the contact check."""
    sc = standard_world()
    sc.hold(0.5)
    sc.grab("hand", "sample_vial")
    _drift(sc, "sample_vial")

    _, alerts = run_events(proc_a, sc.events)
    assert "free_float" not in [kind for kind, _ in alerts]


def test_free_float_advisory_respects_its_cooldown(proc_a: Procedure) -> None:
    """A drifting object drifts for many frames. Say it once."""
    sc = standard_world()
    sc.hold(0.5)
    _drift(sc, "sample_vial", steps=90)

    _, alerts = run_events(proc_a, sc.events)
    assert [kind for kind, _ in alerts].count("free_float") == 1


def test_a_single_position_jump_is_not_free_float(proc_a: Procedure) -> None:
    """Detector jitter and track-id switches teleport objects. Not flight."""
    sc = standard_world()
    sc.hold(0.5)
    x, y, z = sc.objects["sample_vial"].pos
    sc.place("sample_vial", (x + 900.0, y, z))  # one impossible jump
    sc.hold(0.8)

    _, alerts = run_events(proc_a, sc.events)
    assert "free_float" not in [kind for kind, _ in alerts]


def test_untethered_objects_never_raise_free_float(proc_a: Procedure) -> None:
    """tweezers has tether_required unset, so drifting it is not our business."""
    sc = standard_world()
    sc.hold(0.5)
    _drift(sc, "tweezers")

    _, alerts = run_events(proc_a, sc.events)
    assert "free_float" not in [kind for kind, _ in alerts]
