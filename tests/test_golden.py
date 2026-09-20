"""Golden replay corpus.

The primary regression suite for the reasoning layer (TRD section 12.2). Each
case is a recorded world and the verdicts the engine must reach from it.

When a bug is found, add the reproduction here first. A case that survives is
worth more than the fix.
"""

from __future__ import annotations

import pytest

from orbital_har.core.bus import read_stream
from orbital_har.reasoning.schema import Procedure
from orbital_har.simkit.fixtures import ALL_FIXTURES, build
from tests.conftest import PROCEDURES, run_events


@pytest.fixture(scope="session")
def procedures() -> dict[str, Procedure]:
    return {
        "proc_a": Procedure.load(PROCEDURES / "proc_a.yaml"),
        "proc_b": Procedure.load(PROCEDURES / "proc_b.yaml"),
    }


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_fixture_reaches_expected_verdicts(name: str, procedures) -> None:
    fixture = build(name)
    procedure = procedures[fixture.procedure]
    engine, alerts = run_events(procedure, fixture.scenario.events)

    actual = {sid: state.value for sid, state in engine.states.items()}
    for step_id, expected in fixture.expected.final_states.items():
        assert actual[step_id] == expected, (
            f"{name}: step {step_id} is '{actual[step_id]}', expected '{expected}'\n"
            f"full state: {actual}"
        )

    for expected_alert in fixture.expected.alerts:
        assert expected_alert in alerts, f"{name}: expected alert {expected_alert}, got {alerts}"


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_clean_fixtures_raise_no_alerts(name: str, procedures) -> None:
    fixture = build(name)
    if fixture.expected.alerts:
        pytest.skip("this fixture is expected to alert")
    _, alerts = run_events(procedures[fixture.procedure], fixture.scenario.events)
    assert alerts == [], f"{name} should be clean but raised {alerts}"


def test_fixtures_round_trip_through_disk(tmp_path, procedures) -> None:
    """Replay from a written stream must match replay from memory.

    This is the demo-insurance path: if the camera dies, we replay a file.
    """
    fixture = build("proc_a_clean")
    path = fixture.scenario.write(tmp_path / "s.jsonl")

    from_memory, _ = run_events(procedures["proc_a"], fixture.scenario.events)
    from_disk, _ = run_events(procedures["proc_a"], read_stream(path))

    assert from_memory.states == from_disk.states


def test_malformed_stream_is_rejected_loudly(tmp_path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"t":1,"seq":1,"src":"x","type":"frame","payload":{}}\nnot json\n')
    with pytest.raises(ValueError, match="malformed event"):
        list(read_stream(bad))


def test_proc_b_runs_on_the_proc_a_vocabulary(procedures) -> None:
    """Differentiator D-02: no new detector classes, so no retraining."""
    from orbital_har.reasoning.schema import check_vocabulary

    available = procedures["proc_a"].vocabulary_classes
    assert check_vocabulary(procedures["proc_b"], available) == set()
