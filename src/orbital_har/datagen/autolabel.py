"""Zero-shot pre-labelling with an open-vocabulary detector.

This does not produce ground truth and must never be treated as such. It
produces a **first pass for a human to correct**, which is worth doing because
correcting a box is perhaps five times faster than drawing one.

Two choices follow from that:

*The confidence floor is deliberately low.* A missed object has to be drawn from
scratch; a false positive is deleted with one key. Recall is cheaper than
precision here, which is the opposite of the run-time trade-off.

*Every machine label is recorded.* ``.autolabel.json`` lists what was written
and with what confidence, so a reviewer knows exactly which boxes have not yet
been looked at by a person. An unreviewed dataset that looks reviewed is how a
model ends up trained on its own mistakes.

Open-vocabulary detectors cannot see object *state* - nothing in "an open red
box" versus "a small closed red box" is reliably separable zero-shot. Expect the
state classes to need the most correction; that is the part of the vocabulary a
trained model exists to solve.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orbital_har.datagen.dataset import IMAGE_SUFFIXES
from orbital_har.datagen.vocabulary import ClassInfo

#: Low on purpose: deleting a wrong box is faster than drawing a missing one.
DEFAULT_CONF = 0.15


@dataclass
class LabelStats:
    images: int = 0
    labelled: int = 0
    empty: int = 0
    instances: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"{self.labelled}/{self.images} images pre-labelled ({self.empty} with nothing found)"
        ]
        for name, count in sorted(self.instances.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:<20} {count:>5}")
        if self.failures:
            lines.append(f"  {len(self.failures)} image(s) failed to read")
        lines.append("")
        lines.append("These are machine guesses. Correct them before training -")
        lines.append("open/closed states especially, which zero-shot cannot judge.")
        return "\n".join(lines)


def to_yolo_line(index: int, box: tuple[float, float, float, float], w: int, h: int) -> str:
    """xyxy pixels -> ``cls cx cy bw bh`` normalised, clamped to the frame."""
    x0, y0, x1, y1 = box
    x0, x1 = sorted((max(0.0, min(x0, w)), max(0.0, min(x1, w))))
    y0, y1 = sorted((max(0.0, min(y0, h)), max(0.0, min(y1, h))))
    cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    bw, bh = (x1 - x0) / w, (y1 - y0) / h
    return f"{index} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


class AutoLabeller:
    """Pre-labels a folder of frames against the procedure vocabulary."""

    def __init__(
        self,
        classes: list[ClassInfo],
        weights: str = "yolov8s-worldv2.pt",
        conf: float = DEFAULT_CONF,
    ) -> None:
        self.classes = classes
        self.weights = weights
        self.conf = conf
        self._model: Any = None
        #: Prompt order must match class index order, because the model returns
        #: the prompt's position and we map it straight back to a class.
        self.prompts = [c.prompt for c in classes]

    def load(self) -> None:
        from ultralytics import YOLOWorld

        self._model = YOLOWorld(self.weights)
        self._model.set_classes(self.prompts)

    def label_image(self, image: Path) -> list[str] | None:
        """YOLO label lines for one image, or None if it could not be read."""
        import cv2

        frame = cv2.imread(str(image))
        if frame is None:
            return None
        h, w = frame.shape[:2]
        result = self._model.predict(frame, conf=self.conf, verbose=False)[0]

        lines = []
        for box in result.boxes:
            index = int(box.cls[0])
            if not 0 <= index < len(self.classes):
                continue
            xyxy = tuple(float(v) for v in box.xyxy[0])
            lines.append(to_yolo_line(index, xyxy, w, h))  # type: ignore[arg-type]
        return lines

    def label_dir(self, directory: Path) -> LabelStats:
        """Write a ``.txt`` beside every image, plus the review manifest."""
        if self._model is None:
            self.load()

        images = sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        stats = LabelStats(images=len(images))
        manifest: dict[str, Any] = {
            "weights": self.weights,
            "conf": self.conf,
            "classes": [c.name for c in self.classes],
            "reviewed": False,
            "files": {},
        }

        for image in images:
            lines = self.label_image(image)
            if lines is None:
                stats.failures.append(image.name)
                continue
            (directory / f"{image.stem}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
            stats.labelled += 1
            if not lines:
                stats.empty += 1
            for line in lines:
                name = self.classes[int(line.split()[0])].name
                stats.instances[name] = stats.instances.get(name, 0) + 1
            manifest["files"][image.name] = len(lines)

        (directory / ".autolabel.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return stats
