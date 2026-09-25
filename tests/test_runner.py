"""Integration tests for the SessionRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from orbital_har.core.bus import EventBus, read_stream
from orbital_har.reasoning.engine import Engine
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.runner import SessionRunner
from orbital_har.runtime.store import Store
from orbital_har.runtime.telemetry import TelemetryWriter, verify
from tests.conftest import PROCEDURES


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    # Setup test procedure
    proc_path = Path("procedures/proc_a.yaml")
    proc = Procedure.load(proc_path)
    proc_yaml = proc_path.read_text(encoding="utf-8")
    s.upsert_procedure(
        id=proc.procedure.id,
        name=proc.procedure.name,
        version=proc.procedure.version,
        vocabulary=proc.procedure.vocabulary,
        rack_markers=proc.procedure.rack_markers,
        source_path="proc_a.yaml",
        yaml_content=proc_yaml,
        step_count=len(proc.steps),
        steps=[
            {
                "step_id": st.id,
                "ordinal": i,
                "name": st.name,
                "voice_prompt": st.voice,
                "any_of": [],
            }
            for i, st in enumerate(proc.steps)
        ],
    )
    # Create the session
    s.create_session(
        session_id="golden-01",
        procedure_id=proc.procedure.id,
        procedure_version=proc.procedure.version,
        session_dir=str(tmp_path / "golden-01"),
    )
    yield s
    s.close()


def test_session_runner_integration(store: Store, tmp_path: Path) -> None:
    # 1. Setup the Runner components
    proc_path = Path("procedures/proc_a.yaml")
    procedure = Procedure.load(proc_path)

    bus = EventBus()
    engine = Engine(procedure=procedure)
    # Engine is given NO bus initially, the runner binds it!
    assert engine.bus is None

    sess_dir = tmp_path / "golden-01"
    sess_dir.mkdir(exist_ok=True)
    telem_path = sess_dir / "telemetry.jsonl"
    telemetry = TelemetryWriter(telem_path).open()

    mock_ws = Mock()

    runner = SessionRunner(
        session_id="golden-01",
        engine=engine,
        bus=bus,
        store=store,
        telemetry=telemetry,
        ws_publisher=mock_ws,
    )

    # Check runner actually bound the bus
    assert engine.bus is bus

    # 2. Run golden replay stream. Build it here rather than reading a checked-in
    # file: data/ is gitignored, so a fresh clone has no fixture streams.
    from orbital_har.simkit.fixtures import build as build_fixture

    stream_path = build_fixture("proc_a_skip_s4").scenario.write(tmp_path / "proc_a_skip_s4.jsonl")
    stream = read_stream(stream_path)
    runner.replay(stream)
    runner.close()

    # 3. Expected engine verdicts
    assert engine.is_complete is True

    # 4. Telemetry verifies
    res = verify(telem_path)
    assert res.ok is True
    assert res.record_count > 0

    # 5. Store has correct step runs/alerts
    step_runs = store.get_step_runs("golden-01")
    assert len(step_runs) > 0
    assert step_runs[-1]["state"] == "complete"

    # 6. Mock WebSocket publisher received updates
    assert mock_ws.call_count > 0
    last_call_arg = mock_ws.call_args_list[-1][0][0]
    assert "t" in last_call_arg
    assert "type" in last_call_arg


# --------------------------------------------------------------------------
# Headless live run
# --------------------------------------------------------------------------


class _StubCamera:
    """Yields a fixed number of blank frames, then stops."""

    def __init__(self, frames: int) -> None:
        self.remaining = frames
        self.opened = True
        self.failure = None
        self.released = False

    def open(self) -> bool:
        return True

    def read(self):
        import numpy as np

        if self.remaining <= 0:
            return None
        self.remaining -= 1
        return np.zeros((240, 320, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


class _StubDetector:
    """Always reports the same object. Stands in for a trained model."""

    def __init__(self, cls: str) -> None:
        self.cls = cls
        self.open_vocab = False
        self.mode = "stub"

    def detect(self, frame, wanted, min_area=0.06, multi=False):
        from orbital_har.perception.detect import DetectionResult

        return DetectionResult(
            objects=[
                {
                    "cls": self.cls,
                    "conf": 0.95,
                    "bbox": [10.0, 10.0, 110.0, 110.0],
                    "track_id": None,
                }
            ]
        )


def test_start_live_drives_the_engine_from_a_camera(tmp_path: Path) -> None:
    """Perception publishes, the engine consumes, telemetry records it."""
    from orbital_har.core.bus import EventBus
    from orbital_har.perception.pipeline import PerceptionPipeline
    from orbital_har.reasoning.engine import Engine
    from orbital_har.reasoning.schema import Procedure
    from orbital_har.runtime.runner import SessionRunner
    from orbital_har.runtime.store import Store
    from orbital_har.runtime.telemetry import TelemetryWriter, verify

    procedure = Procedure.load(PROCEDURES / "proc_a.yaml")
    store = Store(tmp_path / "s.db")
    store.upsert_procedure(
        id=procedure.procedure.id,
        name=procedure.procedure.name,
        version=1,
        vocabulary="v",
        rack_markers="m",
        source_path="p",
        yaml_content="c",
        step_count=len(procedure.steps),
        steps=[],
    )
    store.create_session(
        session_id="live-01",
        procedure_id=procedure.procedure.id,
        procedure_version=1,
        session_dir=str(tmp_path),
    )

    telemetry = TelemetryWriter(tmp_path / "telemetry.jsonl").open()
    runner = SessionRunner("live-01", Engine(procedure), EventBus(), store, telemetry)
    pipeline = PerceptionPipeline(_StubDetector("outer_box_open"))  # type: ignore[arg-type]
    camera = _StubCamera(frames=25)

    runner.start_live(pipeline, camera, wanted={"outer_box_open"})  # type: ignore[arg-type]

    # s1 needs 12 held frames of outer_box_open; 25 frames is comfortably past.
    assert runner.engine.state_of("s1").value == "complete"
    assert camera.released, "the camera must be released however the run ends"

    session = store.get_session("live-01")
    assert session is not None and session["status"] != "running"
    assert verify(tmp_path / "telemetry.jsonl").ok
    store.close()


def test_start_live_finalizes_even_with_no_frames(tmp_path: Path) -> None:
    """A run that ends unsealed is a run whose telemetry cannot be verified."""
    from orbital_har.core.bus import EventBus
    from orbital_har.perception.pipeline import PerceptionPipeline
    from orbital_har.reasoning.engine import Engine
    from orbital_har.reasoning.schema import Procedure
    from orbital_har.runtime.runner import SessionRunner
    from orbital_har.runtime.store import Store
    from orbital_har.runtime.telemetry import TelemetryWriter, verify

    procedure = Procedure.load(PROCEDURES / "proc_a.yaml")
    store = Store(tmp_path / "s.db")
    store.upsert_procedure(
        id=procedure.procedure.id,
        name="n",
        version=1,
        vocabulary="v",
        rack_markers="m",
        source_path="p",
        yaml_content="c",
        step_count=len(procedure.steps),
        steps=[],
    )
    store.create_session(
        session_id="live-02",
        procedure_id=procedure.procedure.id,
        procedure_version=1,
        session_dir=str(tmp_path),
    )
    telemetry = TelemetryWriter(tmp_path / "telemetry.jsonl").open()
    runner = SessionRunner("live-02", Engine(procedure), EventBus(), store, telemetry)

    runner.start_live(
        PerceptionPipeline(_StubDetector("outer_box_open")),  # type: ignore[arg-type]
        _StubCamera(frames=0),  # type: ignore[arg-type]
        wanted={"outer_box_open"},
    )

    assert store.get_session("live-02")["status"] != "running"
    assert verify(tmp_path / "telemetry.jsonl").ok
    store.close()
