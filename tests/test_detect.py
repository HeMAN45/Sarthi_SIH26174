"""What the detector reports: every presented object, stock and trained together.

Models are stand-ins that return scripted boxes, so no weights are needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from orbital_har.perception.detect import Detector
from orbital_har.perception.pipeline import PerceptionPipeline
from orbital_har.reasoning.schema import Procedure

FRAME = np.zeros((100, 100, 3), np.uint8)  # 10 000 px: a 40x40 box is 16 %


class ScriptedModel:
    """Answers every predict with the same boxes; counts how often it is asked."""

    def __init__(self, names: list[str], boxes: list[tuple[str, tuple[int, int, int, int]]]):
        self.names = dict(enumerate(names))
        self._ids = {n: i for i, n in self.names.items()}
        self._boxes = boxes
        self.calls = 0
        #: The input size of the last predict; None when the model kept its own.
        self.imgsz: int | None = None

    def predict(self, frame, conf, verbose=False, imgsz=None):
        self.calls += 1
        self.imgsz = imgsz
        boxes = [SimpleNamespace(cls=[self._ids[n]], conf=[0.9], xyxy=[b]) for n, b in self._boxes]
        return [SimpleNamespace(boxes=boxes)]


BIG_BOTTLE = ("bottle", (0, 0, 40, 40))  # 16 %, held up close
BIG_PHONE = ("cell phone", (50, 50, 90, 90))  # 16 %
SMALL_CUP = ("cup", (0, 90, 10, 100))  # 1 %, on the table at the back


def stock(*boxes) -> ScriptedModel:
    return ScriptedModel(["bottle", "cell phone", "cup", "book"], list(boxes))


def classes(result) -> list[str]:
    return sorted(o["cls"] for o in result.objects)


def test_two_objects_held_up_at_once_are_both_reported() -> None:
    det = Detector(stock(BIG_BOTTLE, BIG_PHONE, SMALL_CUP))
    result = det.detect(FRAME, {"bottle", "cell phone", "cup"}, min_area=0.06)
    assert classes(result) == ["bottle", "cell phone"]  # the cup is not being shown


def test_a_scene_reports_everything_in_view() -> None:
    det = Detector(stock(BIG_BOTTLE, SMALL_CUP))
    result = det.detect(FRAME, {"bottle", "cup"}, min_area=0.06, multi=True)
    assert classes(result) == ["bottle", "cup"]


def test_only_what_the_procedure_wants_is_reported() -> None:
    det = Detector(stock(BIG_BOTTLE, BIG_PHONE))
    assert classes(det.detect(FRAME, {"bottle"})) == ["bottle"]


@pytest.fixture()
def with_trained(monkeypatch):
    """A stock detector with the operator's trained cap states beside it."""
    base = stock(BIG_BOTTLE, BIG_PHONE)
    mine = ScriptedModel(
        ["bottle_open", "bottle_closed", "bottle"], [("bottle_open", (0, 0, 40, 40))]
    )
    import ultralytics

    monkeypatch.setattr(ultralytics, "YOLO", lambda path: mine)
    det = Detector(base)
    return det, base, mine


def test_trained_objects_join_the_stock_ones(with_trained) -> None:
    det, _, _ = with_trained
    assert det.use_detector("best.pt") == ["bottle_open", "bottle_closed", "bottle"]
    assert {"bottle_open", "bottle_closed", "cell phone", "book"} <= set(det.all_classes)
    assert det.trained_classes == ["bottle", "bottle_closed", "bottle_open"]
    assert det.mode == "detector"


def test_one_frame_can_mix_stock_and_trained_objects(with_trained) -> None:
    det, _, _ = with_trained
    det.use_detector("best.pt")
    result = det.detect(FRAME, {"bottle_open", "cell phone"})
    assert classes(result) == ["bottle_open", "cell phone"]


def test_a_trained_class_answers_for_its_own_name(with_trained) -> None:
    # The operator trained "bottle" on their own bottle: the stock bottle
    # detection must not double it.
    det, _, mine = with_trained
    mine._boxes = [("bottle", (0, 0, 40, 40))]
    det.use_detector("best.pt")
    assert classes(det.detect(FRAME, {"bottle"})) == ["bottle"]


def test_a_model_nothing_is_wanted_from_is_not_run(with_trained) -> None:
    det, base, mine = with_trained
    det.use_detector("best.pt")
    det.detect(FRAME, {"bottle_open"})
    assert (base.calls, mine.calls) == (0, 1)
    det.detect(FRAME, set())  # a body-actions-only procedure
    assert (base.calls, mine.calls) == (0, 1)


def test_the_stock_model_is_not_asked_for_classes_it_was_never_taught() -> None:
    # PROC-A before its model exists: none of its classes are stock names, so
    # running the stock model would cost a full pass and find nothing it wants.
    base = stock(BIG_BOTTLE)
    det = Detector(base)
    assert det.detect(FRAME, {"red_box_open", "sample_vial"}).objects == []
    assert base.calls == 0
    det.detect(FRAME, {"red_box_open", "bottle"})
    assert base.calls == 1


def test_the_input_size_goes_to_the_stock_model_only(with_trained) -> None:
    # A trained detector runs at the size it learned at; forcing another on it
    # would cost it accuracy for nothing.
    det, base, mine = with_trained
    det.use_detector("best.pt")
    det.detect(FRAME, {"bottle_open", "cell phone"}, imgsz=416)
    assert (base.imgsz, mine.imgsz) == (416, None)


def test_the_pipeline_passes_its_input_size_on() -> None:
    base = stock(BIG_BOTTLE)
    pipeline = PerceptionPipeline(Detector(base), imgsz=320)
    pipeline.body = False
    pipeline.configure(want_rack=False, want_pose=False)
    pipeline.observe(FRAME, 1, wanted={"bottle"})
    assert base.imgsz == 320


def test_the_pipeline_reports_everything_for_a_scene_procedure() -> None:
    pipeline = PerceptionPipeline(Detector(stock(BIG_BOTTLE, SMALL_CUP)))
    pipeline.body = False
    pipeline.configure(want_rack=False, want_pose=False)
    presented = pipeline.observe(FRAME, 1, wanted={"bottle", "cup"}).objects
    pipeline.configure(want_rack=False, want_pose=False, scene=True)
    everything = pipeline.observe(FRAME, 2, wanted={"bottle", "cup"}).objects
    assert [o["cls"] for o in presented] == ["bottle"]
    assert sorted(o["cls"] for o in everything) == ["bottle", "cup"]


def test_which_procedures_are_scenes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "procedures"
    assert Procedure.load(root / "drink_water_body.yaml").is_scene  # contact + home spot
    assert Procedure.load(root / "proc_a.yaml").is_scene  # rack markers
    assert not Procedure.load(root / "demo_live.yaml").is_scene  # hold each one up


def test_a_deleted_trained_class_leaves_the_list_and_is_never_reported(with_trained) -> None:
    det, _, _ = with_trained
    det.use_detector("best.pt")
    det.retire({"bottle_open"})
    assert "bottle_open" not in det.all_classes
    assert det.detect(FRAME, {"bottle_open"}).objects == []
    det.use_detector("best.pt")  # retrained: a new model knows only what it learned
    assert "bottle_open" in det.all_classes
