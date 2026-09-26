"""LiveSession lifecycle: the camera belongs to a run.

Ready -> Live -> Complete (docs/03-APP-FLOW.md section 3). Ending a run seals
its hash chain and releases the camera; starting one powers it back on.

These drive the real session loop with a scripted camera and a detector that
sees exactly what the test tells it to, so no device and no model are needed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orbital_har.perception.capture import Camera
from orbital_har.perception.detect import DetectionResult, Detector
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import (
    PHASE_COMPLETE,
    PHASE_LIVE,
    PHASE_READY,
    LiveSession,
    build_procedure,
)
from orbital_har.runtime.voice import PiperSynth, Voice


class ScriptedCamera(Camera):
    """Blank frames while open. Records how often it was powered."""

    def __init__(self, works: bool = True) -> None:
        super().__init__(index=7)
        self.works = works
        self.opens = 0

    def open(self) -> bool:
        self.opens += 1
        self.opened = self.works
        self.failure = None if self.works else "camera 7 unavailable"
        return self.opened

    def read(self) -> np.ndarray | None:
        return np.zeros((48, 64, 3), dtype=np.uint8) if self.opened else None

    def release(self) -> None:
        self.opened = False


class ScriptedDetector(Detector):
    """Sees whatever is in ``self.seen``, at high confidence."""

    def __init__(self, classes: tuple[str, ...] = ("bottle", "cup", "book")) -> None:
        super().__init__(SimpleNamespace(names=dict(enumerate(classes))))
        self.seen: list[str] = []

    def detect(self, frame, wanted, min_area=0.06, multi=False) -> DetectionResult:
        return DetectionResult(
            objects=[
                {"cls": c, "conf": 0.95, "bbox": [0.0, 0.0, 32.0, 24.0], "track_id": None}
                for c in self.seen
            ]
        )


def wait_for(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def rig(tmp_path: Path):
    camera = ScriptedCamera()
    detector = ScriptedDetector()
    proc = build_procedure(["bottle", "cup"], {"bottle", "cup", "book"}, name="Two objects")
    voice = Voice(cache_dir=tmp_path / "voice", synth=PiperSynth(tmp_path / "no-voice"))
    session = LiveSession(
        proc, detector, camera=camera, data_root=tmp_path, record=False, voice=voice
    )
    session.start()
    yield SimpleNamespace(session=session, camera=camera, detector=detector)
    session.stop()
    session.store.close()


def _phase(rig) -> str:
    return rig.session.state().get("phase", "")


def _rows(rig) -> list[dict]:
    return rig.session.store.list_sessions(limit=50)


class TestReady:
    def test_starts_ready_with_the_camera_off(self, rig) -> None:
        assert wait_for(lambda: _phase(rig) == PHASE_READY)
        st = rig.session.state()
        assert st["camera"]["state"] == "off"
        assert rig.camera.opens == 0
        # Nothing was run, so nothing was logged.
        time.sleep(0.2)
        assert _rows(rig) == []

    def test_capture_is_refused_while_the_camera_is_off(self, rig) -> None:
        assert rig.session.capture_raw() is None


class TestRun:
    def test_start_powers_the_camera_and_opens_a_logged_run(self, rig) -> None:
        rig.session.start_run("clean")
        assert wait_for(lambda: _phase(rig) == PHASE_LIVE and rig.session.log is not None)
        assert rig.camera.opened
        assert rig.session.state()["camera"]["state"] == "live"
        assert [r["status"] for r in _rows(rig)] == ["running"]

    def test_end_run_seals_the_chain_and_releases_the_camera(self, rig) -> None:
        rig.session.start_run("clean")
        assert wait_for(lambda: rig.session.log is not None)
        rig.session.end_run()
        assert wait_for(lambda: _phase(rig) == PHASE_READY)

        assert not rig.camera.opened
        assert rig.session.state()["camera"]["state"] == "off"
        assert rig.session.log is not None and rig.session.log.closed
        assert _rows(rig)[0]["status"] == "aborted"
        # The ended run stays on screen as a debrief, with a frozen clock.
        st = rig.session.state()
        assert st["session"]["closed"] is True
        assert len(st["session"]["head"]) == 64
        frozen = st["session"]["elapsed_s"]
        time.sleep(0.25)
        assert rig.session.state()["session"]["elapsed_s"] == frozen
        # A stale frame must not become training data.
        assert rig.session.capture_raw() is None

    def test_a_new_run_after_end_powers_the_camera_again(self, rig) -> None:
        rig.session.start_run("clean")
        assert wait_for(lambda: rig.session.log is not None)
        first = rig.session.log.id
        rig.session.end_run()
        assert wait_for(lambda: _phase(rig) == PHASE_READY)

        rig.session.start_run("strict")
        assert wait_for(
            lambda: (
                _phase(rig) == PHASE_LIVE
                and rig.session.log is not None
                and rig.session.log.id != first
            )
        )
        assert rig.camera.opens == 2
        assert rig.session.state()["mode"] == "strict"

    def test_restart_mid_run_archives_the_old_run_with_its_own_verdicts(self, rig) -> None:
        """Regression: a restart used to seal the old run with the NEW engine,
        so every restarted run was archived as 0 steps complete."""
        rig.session.start_run("clean")
        rig.detector.seen = ["bottle"]
        assert wait_for(
            lambda: any(s["state"] == "complete" for s in rig.session.state().get("steps", []))
        )
        old = rig.session.log.id
        rig.detector.seen = []
        rig.session.start_run("clean")
        assert wait_for(lambda: rig.session.log is not None and rig.session.log.id != old)

        archived = rig.session.store.get_session(old)
        assert archived["status"] == "aborted"
        assert archived["steps_complete"] == 1

    def test_completion_seals_the_run_and_keeps_the_camera_for_the_debrief(self, rig) -> None:
        rig.session.start_run("clean")
        rig.detector.seen = ["bottle", "cup"]
        assert wait_for(lambda: _phase(rig) == PHASE_COMPLETE, timeout=8.0)

        assert rig.session.log.closed
        assert _rows(rig)[0]["status"] == "complete"
        assert rig.camera.opened
        assert rig.session.state()["complete"] is True

    def test_skip_between_runs_is_ignored(self, rig) -> None:
        rig.session.skip()
        time.sleep(0.3)
        assert all(s["state"] == "pending" for s in rig.session.state()["steps"])


class TestCamera:
    def test_preview_perceives_but_judges_nothing(self, rig) -> None:
        rig.detector.seen = ["bottle"]
        rig.session.set_camera(True)
        assert wait_for(lambda: rig.session.state()["camera"]["state"] == "live")
        # The first frame waits on the pose model's first inference, which is
        # slow in a cold process: wait for it rather than for a fixed time.
        assert wait_for(lambda: rig.session.capture_raw() is not None)
        time.sleep(0.4)  # a few more frames: none of them may be judged
        assert _phase(rig) == PHASE_READY
        assert _rows(rig) == []
        assert all(s["state"] == "pending" for s in rig.session.state()["steps"])
        assert rig.session.capture_raw() is not None

    def test_switching_the_camera_off_ends_a_live_run(self, rig) -> None:
        rig.session.start_run("clean")
        assert wait_for(lambda: rig.session.log is not None)
        rig.session.set_camera(False)
        assert wait_for(lambda: _phase(rig) == PHASE_READY)
        assert rig.session.log.closed
        assert not rig.camera.opened

    def test_an_unavailable_camera_is_reported_not_hidden(self, tmp_path: Path) -> None:
        camera = ScriptedCamera(works=False)
        proc = build_procedure(["bottle"], {"bottle"})
        session = LiveSession(
            proc,
            ScriptedDetector(),
            camera=camera,
            data_root=tmp_path,
            record=False,
            voice=Voice(cache_dir=tmp_path / "v", synth=PiperSynth(tmp_path / "none")),
        )
        session.start()
        try:
            session.start_run("clean")
            assert wait_for(lambda: session.state()["camera"]["state"] == "unavailable")
            assert session.state()["camera"]["detail"] == "camera 7 unavailable"
            # No frames, no run on record: an empty session would be a lie.
            time.sleep(0.3)
            assert session.store.list_sessions() == []
        finally:
            session.stop()
            session.store.close()


class TestLoad:
    def test_loading_seals_the_run_and_can_start_the_new_procedure(self, rig) -> None:
        rig.session.start_run("clean")
        assert wait_for(lambda: rig.session.log is not None)
        old = rig.session.log.id

        rig.session.load_procedure(build_procedure(["book"], {"book"}, name="Book"), start=True)
        assert wait_for(
            lambda: (
                rig.session.state()["procedure"] == "Book"
                and rig.session.log is not None
                and rig.session.log.id != old
            )
        )
        assert rig.session.store.get_session(old)["status"] == "aborted"
        assert _phase(rig) == PHASE_LIVE

    def test_loading_without_start_returns_to_ready(self, rig) -> None:
        rig.session.start_run("clean")
        assert wait_for(lambda: rig.session.log is not None)
        rig.session.load_procedure(build_procedure(["book"], {"book"}, name="Book"))
        assert wait_for(lambda: rig.session.state()["procedure"] == "Book")
        assert _phase(rig) == PHASE_READY
        assert not rig.camera.opened
        # The old run's debrief described a procedure that is no longer loaded.
        assert rig.session.state()["session"] is None

    def test_missing_classes_are_named_for_a_fixed_detector(self, rig) -> None:
        seeable = build_procedure(["bottle"], {"bottle"})
        assert rig.session.missing_classes(seeable) == []
        unseeable = build_procedure(["bottle", "red_box_open"], {"bottle", "red_box_open"})
        assert rig.session.missing_classes(unseeable) == ["red_box_open"]


class TestEndpoints:
    """The HTTP surface the dashboard drives, against a real session."""

    @pytest.fixture()
    def client(self, rig):
        from fastapi.testclient import TestClient

        from orbital_har.server.app import app, configure
        from tests.conftest import PROCEDURES

        configure(rig.session.store, session=rig.session, procedures=PROCEDURES)
        return TestClient(app)

    def test_start_and_stop_drive_the_camera(self, client, rig) -> None:
        assert client.post("/api/session/start?mode=clean").json()["ok"]
        assert wait_for(lambda: rig.camera.opened and _phase(rig) == PHASE_LIVE)
        assert client.post("/api/session/stop").json()["ok"]
        assert wait_for(lambda: not rig.camera.opened and _phase(rig) == PHASE_READY)

    def test_camera_preview_toggle(self, client, rig) -> None:
        client.post("/api/camera?on=true")
        assert wait_for(lambda: rig.camera.opened)
        assert _phase(rig) == PHASE_READY
        client.post("/api/camera?on=false")
        assert wait_for(lambda: not rig.camera.opened)

    def test_capture_explains_a_camera_that_is_off(self, client) -> None:
        from orbital_har.runtime.training import TrainManager
        from orbital_har.server import app as server_app

        server_app._trainer = TrainManager(Path(server_app._session.data_root) / "custom")
        r = client.post("/api/train/capture?name=bottle")
        assert r.status_code == 400
        assert "camera is off" in r.json()["error"]

    def test_library_lists_procedures_with_what_they_need(self, client) -> None:
        lib = {p["id"]: p for p in client.get("/api/procedures").json()}
        assert {"proc_a", "proc_b", "demo_live"} <= set(lib)
        assert lib["proc_a"]["rack"] and lib["proc_a"]["pose"]
        assert lib["proc_a"]["title"].startswith("PROC-A")
        # The scripted detector knows bottle/cup/book; the phone is beyond it.
        assert lib["demo_live"]["missing"] == ["cell phone"]
        assert not lib["demo_live"]["rack"] and not lib["demo_live"]["pose"]

    def test_load_refuses_what_the_detector_cannot_see(self, client) -> None:
        r = client.post("/api/procedure/load", json={"id": "demo_live"})
        assert r.status_code == 409
        assert r.json()["missing"] == ["cell phone"]

    def test_load_honours_an_explicit_force(self, client, rig) -> None:
        r = client.post("/api/procedure/load", json={"id": "demo_live", "force": True})
        assert r.status_code == 200
        assert wait_for(lambda: rig.session.state()["procedure_id"] == "demo_live")
        assert _phase(rig) == PHASE_READY

    @pytest.mark.parametrize("bad", ["../secrets", "a/b", "", "x" * 65])
    def test_load_rejects_anything_but_a_library_id(self, client, bad: str) -> None:
        assert client.post("/api/procedure/load", json={"id": bad}).status_code == 400

    def test_unknown_procedure_is_a_404(self, client) -> None:
        assert client.post("/api/procedure/load", json={"id": "nope"}).status_code == 404

    def test_mirror_is_on_by_default_and_toggles(self, client, rig) -> None:
        assert wait_for(lambda: rig.session.state()["camera"]["mirror"] is True)
        client.post("/api/camera/mirror?on=false")
        assert wait_for(lambda: rig.session.state()["camera"]["mirror"] is False)

    def test_library_says_which_steps_use_each_class(self, client) -> None:
        lib = {p["id"]: p for p in client.get("/api/procedures").json()}
        entry = lib["drink_water"]
        assert entry["uses"]["bottle_closed"] == [
            "Pick up the bottle",
            "Close the bottle cap",
            "Put the bottle back in its place",
        ]
        assert "Drink the water" in entry["uses"]["bottle_open"]
        # The stock detector already knows "bottle": only the cap states need training.
        assert entry["train"] == ["bottle_closed", "bottle_open"]

    def test_prepare_creates_every_class_a_procedure_needs(self, client, tmp_path) -> None:
        from orbital_har.runtime.training import TrainManager
        from orbital_har.server import app as server_app

        server_app._trainer = TrainManager(tmp_path / "custom")
        r = client.post("/api/train/prepare?procedure=drink_water").json()
        assert set(r["created"]) == {"background", "bottle_closed", "bottle_open"}

    def test_box_review_lists_suspect_background_photos_too(self, client, tmp_path) -> None:
        from orbital_har.runtime.training import TrainManager
        from orbital_har.server import app as server_app

        tm = TrainManager(tmp_path / "custom")
        for cls in ("book", "background"):
            (tm.images / cls).mkdir()
            for i in range(3):
                cv2.imwrite(str(tm.images / cls / f"000{i}.jpg"), np.zeros((48, 64, 3), np.uint8))
        hit = {"box": [0.1, 0.1, 0.5, 0.5], "conf": 0.8, "label": "book"}
        tm.boxes.data = {
            "boxes": {"book": {"0000.jpg": hit, "0002.jpg": hit}, "background": {"0001.jpg": hit}},
            "excluded": ["background/0001.jpg"],
            "flagged": ["background/0001.jpg"],
            "label": "book",
        }
        server_app._trainer = tm
        r = client.get("/api/train/boxes").json()
        assert r["photos"] == {"book": ["0000.jpg", "0002.jpg"], "background": ["0001.jpg"]}
        assert r["classes"] == {"book": {"total": 3, "found": 2, "usable": 2}}
        tile = client.get("/api/train/boxes/image?cls=background&name=0001.jpg")
        assert tile.status_code == 200 and tile.headers["content-type"] == "image/jpeg"

    def test_deploying_with_a_procedure_the_model_cannot_serve_is_refused(
        self, client, tmp_path, monkeypatch
    ) -> None:
        from orbital_har.runtime.training import TrainManager
        from orbital_har.server import app as server_app

        tm = TrainManager(tmp_path / "custom")
        tm.model_path = "never-loaded.pt"
        monkeypatch.setattr(tm, "model_classes", lambda: ["background", "compass"])
        server_app._trainer = tm
        r = client.post("/api/train/use?procedure=drink_water")
        assert r.status_code == 409
        assert "bottle_open" in r.json()["missing"]

    def test_a_trained_detector_deploys_beside_the_stock_objects(
        self, client, rig, tmp_path, monkeypatch
    ) -> None:
        from orbital_har.runtime.training import TrainManager
        from orbital_har.server import app as server_app

        tm = TrainManager(tmp_path / "custom")
        tm.model_path, tm.kind = "never-loaded.pt", "detect"
        monkeypatch.setattr(tm, "model_classes", lambda: ["bottle_closed", "bottle_open"])
        server_app._trainer = tm
        deployed: list[tuple] = []
        monkeypatch.setattr(rig.session, "use_classifier", lambda *a: deployed.append(a))
        # "bottle" comes from the stock detector the trained one joins.
        r = client.post("/api/train/use?procedure=drink_water")
        assert r.status_code == 200, r.json()
        assert deployed[0][2] == "detect"

    def test_testing_a_model_explains_a_camera_that_is_off(self, client, tmp_path) -> None:
        from orbital_har.runtime.training import TrainManager
        from orbital_har.server import app as server_app

        tm = TrainManager(tmp_path / "custom")
        tm.model_path = "never-loaded.pt"
        server_app._trainer = tm
        r = client.get("/api/train/predict")
        assert r.status_code == 400
        assert "camera is off" in r.json()["error"]

    # ---------------------------------------------------------- experiments

    @pytest.fixture()
    def saving(self, rig, tmp_path):
        from fastapi.testclient import TestClient

        from orbital_har.server.app import app, configure
        from tests.conftest import PROCEDURES

        configure(
            rig.session.store,
            session=rig.session,
            procedures=PROCEDURES,
            experiments=tmp_path / "experiments",
        )
        return TestClient(app)

    def test_save_list_run_edit_delete(self, saving, rig) -> None:
        steps = [{"object": "cup", "instruction": "Show the cup"}, {"gesture": "hand_raised"}]
        r = saving.post("/api/experiments", json={"name": "My check", "steps": steps}).json()
        assert r["ok"] and r["id"] == "my_check"

        lib = {p["id"]: p for p in saving.get("/api/procedures").json()}
        assert lib["my_check"]["source"] == "saved"
        assert lib["demo_live"]["source"] == "builtin"
        assert lib["my_check"]["steps"] == ["Show the cup", "Raise your hand"]

        assert saving.post("/api/procedure/load", json={"id": "my_check", "start": True}).json()[
            "ok"
        ]
        assert wait_for(lambda: rig.session.state()["procedure_id"] == "my_check")
        assert wait_for(lambda: rig.session.phase == PHASE_LIVE)

        got = saving.get("/api/experiments/my_check").json()
        assert got["name"] == "My check" and got["steps"][1]["gesture"] == "hand_raised"
        edited = {"name": "My check", "steps": steps[:1], "id": "my_check"}
        assert saving.post("/api/experiments", json=edited).json()["id"] == "my_check"
        assert len(saving.get("/api/experiments/my_check").json()["steps"]) == 1

        assert saving.delete("/api/experiments/my_check").json()["ok"]
        assert "my_check" not in {p["id"] for p in saving.get("/api/procedures").json()}

    def test_a_saved_name_never_shadows_a_builtin(self, saving) -> None:
        r = saving.post(
            "/api/experiments", json={"name": "demo live", "steps": [{"object": "cup"}]}
        )
        assert r.json()["id"] == "demo_live_2"

    def test_saving_explains_what_is_wrong(self, saving) -> None:
        r = saving.post("/api/experiments", json={"name": "  ", "steps": [{"object": "cup"}]})
        assert r.status_code == 400 and "name" in r.json()["error"]
        r = saving.post("/api/experiments", json={"name": "x", "steps": [{"instruction": "?"}]})
        assert r.status_code == 400 and "step 1" in r.json()["error"]

    def test_run_once_with_body_actions_without_saving(self, client, rig) -> None:
        steps = [{"gesture": "hands_together", "object": "cup"}]
        assert client.post("/api/build", json={"name": "Once", "steps": steps}).json()["ok"]
        assert wait_for(lambda: rig.session.state()["procedure"] == "Once")

    def test_run_once_refuses_an_object_the_detector_cannot_see(self, client, rig) -> None:
        steps = [{"object": "cup"}, {"object": "lunar sample"}]
        r = client.post("/api/build", json={"name": "Nope", "steps": steps})
        assert r.status_code == 400
        assert "lunar sample" in r.json()["error"]

    def test_body_tracking_toggle_reaches_the_state(self, client, rig) -> None:
        client.post("/api/body?on=false")
        assert wait_for(lambda: rig.session.state()["perception"]["body"]["enabled"] is False)
        client.post("/api/body?on=true")
        assert wait_for(lambda: rig.session.state()["perception"]["body"]["enabled"] is True)


def test_trained_objects_that_will_not_load_are_reported_not_fatal(rig, monkeypatch, capsys):
    def corrupt(path: str) -> list[str]:
        raise RuntimeError("not a model")

    monkeypatch.setattr(rig.session.detector, "use_detector", corrupt)
    assert rig.session.add_trained("best.pt") == []
    assert "could not load your trained objects" in capsys.readouterr().out
    assert rig.session.detector.all_classes == ["book", "bottle", "cup"]  # stock still there


def test_a_scene_procedure_has_every_object_reported(rig) -> None:
    proc = Procedure.load(Path(__file__).resolve().parents[1] / "procedures" / "drink_water.yaml")
    rig.session._configure_for(proc)
    assert rig.session.pipeline.scene is True
    rig.session._configure_for(build_procedure(["cup"], {"cup"}))
    assert rig.session.pipeline.scene is False
