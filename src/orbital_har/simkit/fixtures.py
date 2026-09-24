"""Golden corpus scenarios.

Each builder returns a Scenario plus the verdicts the engine is expected to
reach. These pairs are the primary regression suite for the reasoning layer
(TRD section 12.2) -- and the demo fallback if a camera dies in front of a jury.

Add a case here whenever a bug is found: a reproduction that survives is worth
more than the fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orbital_har.simkit.scenario import (
    CLIP_HOME,
    MARKER_R,
    MARKER_Y,
    STOWAGE,
    VERIFY_HOLD,
    Scenario,
    standard_world,
)


@dataclass
class ExpectedOutcome:
    """What the engine must conclude. Absent steps are not asserted."""

    final_states: dict[str, str]
    alerts: list[tuple[str, str]] = field(default_factory=list)
    """(kind, step_id) pairs that must appear, in order."""

    #: Optional exact verdict-event sequence. Empty means "do not assert the
    #: full stream" -- final states plus the alert list are the contract for
    #: most cases, and pinning every event makes fixtures brittle.
    verdicts: list = field(default_factory=list)


@dataclass
class Fixture:
    name: str
    procedure: str
    scenario: Scenario
    expected: ExpectedOutcome
    description: str = ""


# --------------------------------------------------------------------------
# PROC-A
# --------------------------------------------------------------------------


def _proc_a_through_s3(sc: Scenario) -> None:
    """Steps 1-3: open the container, stage both boxes on their markers."""
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)

    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.7)

    sc.grab("hand", "yellow_box").hold(0.5)
    sc.place("yellow_box", MARKER_Y).hold(0.5)
    sc.release("hand", "yellow_box").hold(0.7)


def proc_a_clean() -> Fixture:
    sc = standard_world()
    _proc_a_through_s3(sc)

    sc.set_class("red_box", "red_box_open").hold(1.0)

    sc.grab("tweezers", "sample_vial").hold(0.5)
    sc.place("sample_vial", MARKER_Y).hold(0.5)
    sc.release("tweezers", "sample_vial").hold(0.7)

    sc.set_class("red_box", "red_box_closed")
    sc.place("tether_clip", MARKER_R).hold(1.0)

    return Fixture(
        name="proc_a_clean",
        procedure="proc_a",
        scenario=sc,
        description="All six steps in order, strong evidence throughout.",
        expected=ExpectedOutcome(
            final_states={
                "s1": "complete",
                "s2": "complete",
                "s3": "complete",
                "s4": "complete",
                "s5": "complete",
                "s6": "complete",
            },
            alerts=[],
        ),
    )


def proc_a_skip_s4() -> Fixture:
    """The operator never opens the red box but transfers the vial anyway."""
    sc = standard_world()
    _proc_a_through_s3(sc)

    # s4 omitted entirely: red_box stays closed.
    sc.grab("tweezers", "sample_vial").hold(0.5)
    sc.place("sample_vial", MARKER_Y).hold(0.5)
    sc.release("tweezers", "sample_vial").hold(0.7)

    sc.place("tether_clip", MARKER_R).hold(1.0)

    return Fixture(
        name="proc_a_skip_s4",
        procedure="proc_a",
        scenario=sc,
        description="Step 4 passed over; step 5 performed. Must raise a skip alert.",
        expected=ExpectedOutcome(
            final_states={
                "s1": "complete",
                "s2": "complete",
                "s3": "complete",
                "s4": "skipped",
                "s5": "complete",
            },
            alerts=[("skip", "s4")],
        ),
    )


def proc_a_unverified_s4() -> Fixture:
    """Evidence for step 4 exists but is too weak to act on.

    The engine must say so rather than advance on a guess -- FR-18.
    """
    sc = standard_world()
    _proc_a_through_s3(sc)

    sc.set_class("red_box", "red_box_open")
    sc.set_conf("red_box", 0.42)
    sc.hold(3.0)

    return Fixture(
        name="proc_a_unverified_s4",
        procedure="proc_a",
        scenario=sc,
        description="Step 4 evidence below the abstain threshold. Must not complete.",
        expected=ExpectedOutcome(
            final_states={
                "s1": "complete",
                "s2": "complete",
                "s3": "complete",
                "s4": "unverified",
            },
            alerts=[("unverified", "s4")],
        ),
    )


def proc_a_rack_lost() -> Fixture:
    """Markers occluded mid-run: distance predicates must degrade, not assert."""
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)

    sc.rack_lost(True)
    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(1.5)
    sc.release("hand", "red_box").hold(0.5)

    return Fixture(
        name="proc_a_rack_lost",
        procedure="proc_a",
        scenario=sc,
        description="Rack lock lost. Step 2 must NOT complete without positions.",
        expected=ExpectedOutcome(
            final_states={"s1": "complete", "s2": "active"},
            alerts=[],
        ),
    )


# --------------------------------------------------------------------------
# PROC-B
# --------------------------------------------------------------------------


def proc_b_clean() -> Fixture:
    """Full ten-step run, including the order-independent group and the dwell."""
    sc = standard_world()
    sc.hold(1.0)
    sc.set_class("outer_box", "outer_box_open").hold(1.0)

    sc.grab("hand", "yellow_box").hold(0.5)
    sc.place("yellow_box", MARKER_Y).hold(0.5)
    sc.release("hand", "yellow_box").hold(0.7)

    sc.grab("hand", "red_box").hold(0.5)
    sc.place("red_box", MARKER_R).hold(0.5)
    sc.release("hand", "red_box").hold(0.7)

    # g1 members, performed red-first to prove order independence.
    sc.set_class("red_box", "red_box_open").hold(0.8)
    sc.set_class("yellow_box", "yellow_box_open").hold(0.8)

    sc.grab("tweezers", "sample_vial").hold(0.5)
    sc.place("sample_vial", MARKER_Y).hold(0.7)
    sc.release("tweezers", "sample_vial").hold(0.5)

    # s7: hold the vial in the verification band for longer than 2 s.
    sc.place("sample_vial", VERIFY_HOLD).hold(2.8)

    sc.grab("tweezers", "sample_vial").hold(0.5)
    sc.place("sample_vial", MARKER_R).hold(0.7)
    sc.release("tweezers", "sample_vial").hold(0.5)

    sc.set_class("red_box", "red_box_closed")
    sc.set_class("yellow_box", "yellow_box_closed").hold(1.0)

    sc.place("red_box", STOWAGE)
    sc.place("yellow_box", STOWAGE)
    sc.set_class("outer_box", "outer_box_closed").hold(1.0)

    return Fixture(
        name="proc_b_clean",
        procedure="proc_b",
        scenario=sc,
        description="PROC-B end to end. Same vocabulary as PROC-A, no retraining.",
        expected=ExpectedOutcome(
            final_states={f"s{i}": "complete" for i in range(1, 11)},
            alerts=[],
        ),
    )


ALL_FIXTURES = {
    "proc_a_clean": proc_a_clean,
    "proc_a_skip_s4": proc_a_skip_s4,
    "proc_a_unverified_s4": proc_a_unverified_s4,
    "proc_a_rack_lost": proc_a_rack_lost,
    "proc_b_clean": proc_b_clean,
}


def build(name: str) -> Fixture:
    if name not in ALL_FIXTURES:
        raise KeyError(f"unknown fixture '{name}'; have {sorted(ALL_FIXTURES)}")
    return ALL_FIXTURES[name]()


__all__ = [
    "ALL_FIXTURES",
    "CLIP_HOME",
    "ExpectedOutcome",
    "Fixture",
    "build",
]
