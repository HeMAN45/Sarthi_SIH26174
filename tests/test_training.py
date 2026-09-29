"""On-device training: clean data in, honest numbers out.

The failure these guard against was reported from real use: fifty photos of an
object, twenty of a blank wall, and then a raised finger completed the step.
The model did what it was taught. The tooling let it be taught that.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orbital_har.perception.detect import (
    CLS_MIN_CONF,
    DetectionResult,
    Detector,
    classify_verdict,
)
from orbital_har.perception.pipeline import PerceptionPipeline
from orbital_har.runtime.training import TrainManager

NAMES = {0: "background", 1: "compass"}


def _jpeg(value: int = 128) -> bytes:
    img = np.full((48, 64, 3), value, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _fill(tm: TrainManager, name: str, n: int) -> None:
    tm.add_images(name, [_jpeg(10 + i) for i in range(n)])


# ------------------------------------------------------------------ verdicts


class TestVerdict:
    def test_background_is_never_a_detection(self) -> None:
        v = classify_verdict(NAMES, [0.97, 0.03])
        assert not v.accepted and v.why == "nothing presented"

    def test_low_confidence_is_ignored(self) -> None:
        v = classify_verdict({0: "background", 1: "a", 2: "b"}, [0.2, CLS_MIN_CONF - 0.05, 0.25])
        assert not v.accepted and v.why.startswith("low confidence")

    def test_a_near_tie_is_ignored(self) -> None:
        v = classify_verdict({0: "a", 1: "b"}, [0.61, 0.39])
        assert v.accepted  # margin 0.22 clears the bar
        v = classify_verdict({0: "background", 1: "a", 2: "b"}, [0.0, 0.62, 0.38 - 0.0])
        assert v.accepted
        v = classify_verdict({0: "a", 1: "b", 2: "c"}, [0.66, 0.54, 0.0])
        assert not v.accepted and v.why.startswith("ambiguous")

    def test_everything_is_ranked_most_likely_first(self) -> None:
        v = classify_verdict(NAMES, [0.1, 0.9])
        assert v.accepted and v.name == "compass"
        assert [n for n, _ in v.ranked] == ["compass", "background"]


# ------------------------------------------------------------------- dataset


class TestDataset:
    def test_validation_never_contains_a_training_image(self, tmp_path: Path) -> None:
        tm = TrainManager(tmp_path)
        _fill(tm, "compass", 23)
        _fill(tm, "background", 4)
        ds = tm._build_split()
        for cls in ("compass", "background"):
            train = {p.name for p in (ds / "train" / cls).glob("*.jpg")}
            val = {p.name for p in (ds / "val" / cls).glob("*.jpg")}
            assert val, f"{cls} needs at least one held-out image"
            assert not train & val, f"{cls}: validation leaked into training"
            assert len(train) + len(val) == len(list((tm.images / cls).glob("*.jpg")))

    def test_new_captures_never_overwrite_after_a_delete(self, tmp_path: Path) -> None:
        tm = TrainManager(tmp_path)
        _fill(tm, "compass", 3)
        (tm.images / "compass" / "0000.jpg").unlink()
        _fill(tm, "compass", 1)
        names = sorted(p.name for p in (tm.images / "compass").glob("*.jpg"))
        assert names == ["0001.jpg", "0002.jpg", "0003.jpg"]

    def test_prepare_creates_what_a_procedure_needs_plus_background(self, tmp_path: Path) -> None:
        tm = TrainManager(tmp_path)
        created = tm.prepare(["holding_open", "drinking"])
        assert sorted(created) == ["background", "drinking", "holding_open"]
        assert tm.prepare(["holding_open", "drinking"]) == []  # idempotent


class TestAdvice:
    def test_the_reported_mistake_is_named_before_training(self, tmp_path: Path) -> None:
        """50 compass photos, 20 of a wall: exactly what trains fine and fails live."""
        tm = TrainManager(tmp_path)
        _fill(tm, "compass", 50)
        _fill(tm, "background", 20)
        assert tm.can_train() == (True, "")  # the hard rules pass...
        advice = " ".join(tm.advice())
        assert "at least as many" in advice  # ...but background is outnumbered
        assert "hands" in advice  # and the one-target trap is spelled out

    def test_thin_classes_are_flagged(self, tmp_path: Path) -> None:
        tm = TrainManager(tmp_path)
        _fill(tm, "compass", 5)
        _fill(tm, "background", 40)
        assert any("compass" in a and "Under" in a for a in tm.advice())

    def test_a_balanced_varied_set_draws_no_warning_about_balance(self, tmp_path: Path) -> None:
        tm = TrainManager(tmp_path)
        _fill(tm, "open", 25)
        _fill(tm, "closed", 25)
        _fill(tm, "background", 40)
        assert not any("at least as many" in a for a in tm.advice())


# ------------------------------------------------------------ clean capture


class _Painter(Detector):
    """Reports one kept box on the left of the frame."""

    def __init__(self) -> None:
        super().__init__(SimpleNamespace(names={0: "compass"}))

    def detect(self, frame, wanted, min_area=0.06, multi=False, imgsz=None) -> DetectionResult:
        from orbital_har.perception.detect import Candidate

        box = (4.0, 10.0, 20.0, 40.0)
        out = DetectionResult(candidates=[Candidate("compass", 0.9, box, 0.2, kept=True)])
        out.objects.append({"cls": "compass", "conf": 0.9, "bbox": list(box), "track_id": None})
        return out


class TestOverlay:
    def test_perception_never_draws_on_the_frame_it_was_given(self) -> None:
        """The overlay used to be drawn into the camera buffer, so training
        photos captured from the live view carried boxes and labels."""
        frame = np.full((60, 80, 3), 40, dtype=np.uint8)
        before = frame.copy()
        obs = PerceptionPipeline(_Painter()).observe(frame, 1, wanted={"compass"})
        assert np.array_equal(frame, before)
        assert not np.array_equal(obs.frame, before)  # the copy is annotated

    @pytest.mark.parametrize("mirror", [False, True])
    def test_mirroring_moves_the_picture_not_the_evidence(self, mirror: bool) -> None:
        frame = np.zeros((60, 80, 3), dtype=np.uint8)
        obs = PerceptionPipeline(_Painter()).observe(frame, 1, wanted={"compass"}, mirror=mirror)

        green = obs.frame[:, :, 1] > 200
        cols = np.where(green.any(axis=0))[0]
        assert cols.size, "the box must be drawn"
        drawn_left = bool(cols.mean() < frame.shape[1] / 2)
        assert drawn_left == (not mirror)

        detections = [p for _, t, p in obs.emissions if t == "detection"]
        assert detections[0]["objects"][0]["bbox"] == [4.0, 10.0, 20.0, 40.0]


class TestModelCache:
    def test_a_retrained_model_is_the_one_tested(self, tmp_path: Path, monkeypatch) -> None:
        """Regression: every retrain writes the same best.pt, and a path-only
        cache kept testing the first model after it had been replaced."""
        import os

        weights = tmp_path / "best.pt"
        weights.write_bytes(b"first")
        loads: list[str] = []
        monkeypatch.setattr(
            TrainManager, "_load", staticmethod(lambda p: loads.append(Path(p).read_bytes()) or p)
        )
        tm = TrainManager(tmp_path / "custom")
        tm.model_path = str(weights)

        tm._model()
        tm._model()
        assert loads == [b"first"]  # cached while unchanged

        weights.write_bytes(b"second")
        st = weights.stat()
        os.utime(weights, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        tm._model()
        assert loads == [b"first", b"second"]


def test_the_last_model_survives_a_restart(tmp_path: Path) -> None:
    """Training costs minutes on a laptop; a restart must not throw it away."""
    import json

    root = tmp_path / "custom"
    weights = root / "runs" / "cls" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"weights")
    report = {"accuracy": 0.9, "per_class": {"compass": {"correct": 9, "total": 10}}}
    record = {"model": str(weights), "epochs": 20, "report": report}
    (root / "model.json").write_text(json.dumps(record))

    tm = TrainManager(root)
    assert tm.model_path == str(weights)
    assert tm.status["state"] == "done" and tm.status["report"] == report

    weights.unlink()  # a record pointing at nothing is ignored, not trusted
    assert TrainManager(root).model_path is None
