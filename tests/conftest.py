from __future__ import annotations

from pathlib import Path

import pytest

from orbital_har.core.types import EventType
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure

ROOT = Path(__file__).resolve().parents[1]
PROCEDURES = ROOT / "procedures"


@pytest.fixture(scope="session")
def proc_a() -> Procedure:
    return Procedure.load(PROCEDURES / "proc_a.yaml")


@pytest.fixture(scope="session")
def proc_b() -> Procedure:
    return Procedure.load(PROCEDURES / "proc_b.yaml")


def run_events(procedure: Procedure, events, config: EngineConfig | None = None):
    """Drive an engine to completion over a list of events.

    Returns (engine, alerts) where alerts is a list of (kind, step_id).
    """
    engine = Engine(procedure, config)
    alerts: list[tuple[str, str | None]] = []

    def collect(emitted) -> None:
        for out in emitted:
            if out.type == EventType.ALERT.value:
                alerts.append((out.payload["kind"], out.payload.get("step_id")))

    for event in events:
        collect(engine.on_event(event))
    collect(engine.flush())
    return engine, alerts


def run_verdicts(procedure: Procedure, events, config: EngineConfig | None = None):
    """Drive an engine to completion over a list of events.

    Returns (engine, verdicts) where verdicts is a list of all emitted verdict Events.
    """
    engine = Engine(procedure, config)
    verdicts = []

    for event in events:
        verdicts.extend(engine.on_event(event))
    verdicts.extend(engine.flush())
    return engine, verdicts
