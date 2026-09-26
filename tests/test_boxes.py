"""Box proposals for detector training: the object, not the scene around it.

The stock detector is a stand-in here -- each photo is a flat grey whose level
names a scripted scene -- so these run without a model on disk.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from orbital_har.runtime.boxes import BoxProposals

W, H = 640, 480
BOOK = (200, 150, 400, 400)  # a plausible object: 12 % of the frame, upright
PAGE = (200, 150, 330, 400)  # half of it
STRIP = (0, 440, 640, 478)  # the table edge along the bottom of the frame


class FakeDetector:
    def __init__(self, scenes: dict[int, list[tuple[str, float, tuple[int, ...]]]]) -> None:
        self.names = {0: "person", 1: "book", 2: "bench", 3: "frisbee"}
        self.scenes = scenes
        self.ids = {v: k for k, v in self.names.items()}

    def predict(self, frame: np.ndarray, conf: float, verbose: bool = False) -> list:
        scene = round(float(frame.mean()) / 10)
        boxes = [
            SimpleNamespace(cls=[self.ids[label]], conf=[c], xyxy=[box])
            for label, c, box in self.scenes.get(scene, [])
            if c >= conf
        ]
        return [SimpleNamespace(boxes=boxes)]


def _proposals(tmp_path: Path, layout: dict[str, list[int]]) -> BoxProposals:
    images = tmp_path / "images"
    for cls, scenes in layout.items():
        (images / cls).mkdir(parents=True)
        for i, scene in enumerate(scenes):
            flat = np.full((H, W, 3), scene * 10, np.uint8)
            cv2.imwrite(str(images / cls / f"{i:04d}.jpg"), flat)
    return BoxProposals(images, tmp_path / "boxes.json")


# Scenes 1-3 teach the proposer that this object is usually called "book".
USUAL = {
    1: [("book", 0.8, BOOK)],
    2: [("book", 0.7, BOOK)],
    3: [("book", 0.6, BOOK), ("person", 0.9, (0, 0, 300, 480))],
}


def test_the_usual_label_beats_a_stronger_stranger(tmp_path):
    scenes = {**USUAL, 4: [("frisbee", 0.5, PAGE), ("book", 0.3, BOOK)]}
    boxes = _proposals(tmp_path, {"open_book": [1, 2, 3, 4]})
    summary = boxes.propose(["open_book"], None, model=FakeDetector(scenes))

    assert summary["label"] == "book"
    assert boxes.data["boxes"]["open_book"]["0003.jpg"]["label"] == "book"
    assert boxes.box_for("open_book", "0003.jpg") == (0.3125, 0.3125, 0.625, 0.83333)


def test_a_strip_along_the_frame_is_never_the_object(tmp_path):
    scenes = {**USUAL, 4: [("bench", 0.6, STRIP)]}
    boxes = _proposals(tmp_path, {"close_book": [1, 2, 3, 4]})
    summary = boxes.propose(["close_book"], None, model=FakeDetector(scenes))

    assert boxes.box_for("close_book", "0003.jpg") is None
    assert summary["classes"]["close_book"] == {"total": 4, "found": 3, "usable": 3}


def test_people_are_never_proposed(tmp_path):
    boxes = _proposals(tmp_path, {"book": [3]})
    boxes.propose(["book"], None, model=FakeDetector(USUAL))
    assert boxes.data["boxes"]["book"]["0000.jpg"]["label"] == "book"


def test_a_faint_box_with_the_usual_label_is_rescued_and_marked(tmp_path):
    scenes = {**USUAL, 4: [("book", 0.08, BOOK)], 5: [("frisbee", 0.08, BOOK)]}
    boxes = _proposals(tmp_path, {"close_book": [1, 2, 3, 4, 5]})
    summary = boxes.propose(["close_book"], None, model=FakeDetector(scenes))

    assert boxes.box_for("close_book", "0003.jpg") is not None  # a faint "book": kept
    assert boxes.box_for("close_book", "0004.jpg") is None  # a faint anything else: not
    assert summary["weak"] == ["close_book/0003.jpg"]
    assert summary["classes"]["close_book"]["usable"] == 4


def test_a_background_photo_showing_the_object_is_flagged_and_left_out(tmp_path):
    scenes = {**USUAL, 6: [("book", 0.55, BOOK)], 7: [("book", 0.1, BOOK)], 8: []}
    boxes = _proposals(tmp_path, {"book": [1, 2, 3], "background": [6, 7, 8]})
    summary = boxes.propose(["book"], "background", model=FakeDetector(scenes))
    assert summary["flagged"] == ["background/0000.jpg"]  # the faint one is not proof

    counts = boxes.build(tmp_path / "ds", ["book"], "background", val_every=5)
    assert counts == {"book": 3, "background": 2}
    labels = sorted((tmp_path / "ds" / "labels").rglob("*.txt"))
    negatives = [p for p in labels if p.name.startswith("background__")]
    assert {p.stem for p in negatives} == {"background__0001", "background__0002"}
    assert all(p.read_text() == "" for p in negatives)
    assert all(p.read_text().startswith("0 ") for p in labels if p.name.startswith("book__"))


def test_a_flagged_background_photo_can_be_reviewed_and_used_anyway(tmp_path):
    scenes = {**USUAL, 6: [("book", 0.55, BOOK)]}
    boxes = _proposals(tmp_path, {"book": [1, 2, 3], "background": [6, 8]})
    summary = boxes.propose(["book"], "background", model=FakeDetector(scenes))
    assert "background/0000.jpg" in summary["excluded"]  # left out by default
    assert boxes.box_for("background", "0000.jpg") is not None  # drawn on its tile

    assert boxes.toggle("background/0000.jpg") is False  # the operator says: it is not a book
    counts = boxes.build(tmp_path / "ds", ["book"], "background", val_every=5)
    assert counts["background"] == 2


def test_exclusions_survive_a_second_look(tmp_path):
    boxes = _proposals(tmp_path, {"book": [1, 2, 3]})
    model = FakeDetector(USUAL)
    boxes.propose(["book"], None, model=model)
    assert boxes.toggle("book/0001.jpg") is True

    again = BoxProposals(boxes.images, boxes.record)  # a restart
    summary = again.propose(["book"], None, model=model)
    assert summary["excluded"] == ["book/0001.jpg"]
    assert summary["classes"]["book"]["usable"] == 2
