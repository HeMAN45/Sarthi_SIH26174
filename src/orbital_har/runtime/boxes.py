"""Boxes for detector training, proposed by the stock detector and reviewed.

A whole-frame classifier learns whatever differs between its photo folders --
the arm reaching in from the left, the person sitting in the middle -- and then
calls an empty arm "book". A detector learns *where* the object is: everything
outside its box, the hand holding it included, is background by construction.

Drawing boxes by hand is the cost that usually rules detectors out. Here the
stock COCO model, already on disk, proposes them. It first learns what it calls
the object ("book"), then prefers a box with that name in every photo -- a weak
"book" beats a confident "bench" that is really the table edge -- and takes a
closer, lower-confidence look for that name alone where nothing was found.
Those faint boxes are marked for review. The folder still says which *state*
the object is in, so the trained detector's classes are the states themselves
(invariant #8). Photos where nothing plausible is found are left out rather
than mislabelled, and every proposal can be reviewed and excluded before
training.

An open-vocabulary model would name arbitrary objects, but its text encoder is
a download on first use -- a network call this product may not make.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np

#: Stock COCO weights: local, and they already localise books, bottles, cups.
PROPOSER_WEIGHTS = "yolo11n.pt"
PROPOSE_CONF = 0.2
#: The closer look: a photo with nothing at PROPOSE_CONF may still hold a faint
#: box wearing the object's usual label. The folder already says the object is
#: there; the stock model only has to say where. Such boxes are marked weak.
RESCUE_CONF = 0.04
#: A proposal filling almost nothing or almost everything is not the object.
MIN_AREA, MAX_AREA = 0.01, 0.7
#: Nor is a sliver: a thin strip along the frame is a table edge or a shelf.
MAX_ASPECT = 5.0
#: How much a box wearing the object's usual label is preferred over a
#: stronger box of anything else. COCO calls an open book at an angle a
#: "frisbee" now and then; it rarely calls a table edge a "book".
USUAL_LABEL_BONUS = 0.35
#: A background photo where the proposer sees the targets' usual label this
#: confidently probably shows the object -- which would teach "object = nothing".
CONTAMINATION_CONF = 0.4

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the frame


class BoxProposals:
    """Proposed boxes per photo, with the operator's exclusions, on disk."""

    def __init__(self, images: Path, record: Path) -> None:
        self.images = images
        self.record = record
        self.data: dict[str, Any] = {"boxes": {}, "excluded": [], "flagged": [], "label": None}
        if record.is_file():
            with contextlib.suppress(ValueError):  # a corrupt record starts afresh
                self.data = json.loads(record.read_text(encoding="utf-8"))

    # ------------------------------------------------------------- propose

    def propose(self, targets: list[str], background: str | None, model: Any = None) -> dict:
        """Find the object in every target photo; flag suspicious background ones.

        Two passes. The first learns what the stock detector usually calls
        this object ("book"); the second picks, in every photo, the box
        wearing that label when there is one -- a lower-scoring "book" beats a
        confident "bench" that is really the table edge.
        """
        if model is None:
            from ultralytics import YOLO

            model = YOLO(PROPOSER_WEIGHTS)
        found: dict[str, dict[str, list[dict[str, Any]]]] = {}
        labels: Counter[str] = Counter()
        for cls in targets:
            found[cls] = {}
            for img in sorted((self.images / cls).glob("*.jpg")):
                cands = self._candidates(model, img)
                found[cls][img.name] = cands
                strong = [c for c in cands if c["conf"] >= PROPOSE_CONF]
                if strong:
                    labels[max(strong, key=lambda c: c["conf"])["label"]] += 1
        usual = labels.most_common(1)[0][0] if labels else None

        def pick(cands: list[dict[str, Any]]) -> dict[str, Any] | None:
            strong = [c for c in cands if c["conf"] >= PROPOSE_CONF]
            if strong:
                return max(
                    strong,
                    key=lambda c: c["conf"] + (USUAL_LABEL_BONUS if c["label"] == usual else 0.0),
                )
            faint = [c for c in cands if c["label"] == usual]
            return {**max(faint, key=lambda c: c["conf"]), "weak": True} if faint else None

        boxes: dict[str, dict[str, dict[str, Any]]] = {}
        for cls, per in found.items():
            boxes[cls] = {}
            for name, cands in per.items():
                chosen = pick(cands)
                if chosen is not None:
                    boxes[cls][name] = chosen

        flagged: list[str] = []
        if background and usual:
            suspects: dict[str, dict[str, Any]] = {}
            for img in sorted((self.images / background).glob("*.jpg")):
                hits = [c for c in self._candidates(model, img) if c["label"] == usual]
                strongest = max(hits, key=lambda c: c["conf"], default=None)
                if strongest is not None and strongest["conf"] >= CONTAMINATION_CONF:
                    flagged.append(f"{background}/{img.name}")
                    suspects[img.name] = strongest  # drawn on its review tile
            if suspects:
                boxes[background] = suspects

        kept = set(self.data.get("excluded", []))
        live = {f"{c}/{n}" for c in targets for n in boxes.get(c, {})}
        self.data = {
            "boxes": boxes,
            # A suspect background photo starts left out; the operator can bring
            # it back from the review grid if the detector was wrong about it.
            "excluded": sorted((kept & live) | set(flagged)),
            "flagged": flagged,
            "label": usual,
        }
        self.save()
        return self.summary(targets)

    @staticmethod
    def _candidates(model: Any, img: Path) -> list[dict[str, Any]]:
        """Every plausible object box in one photo: not a person, not a sliver."""
        frame = cv2.imread(str(img))
        if frame is None:
            return []
        h, w = frame.shape[:2]
        result = model.predict(frame, conf=RESCUE_CONF, verbose=False)[0]
        out = []
        for b in result.boxes:
            label = model.names[int(b.cls[0])]
            if label == "person":
                continue
            x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
            bw, bh = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
            if not MIN_AREA <= bw * bh / float(w * h) <= MAX_AREA:
                continue
            if max(bw / bh, bh / bw) > MAX_ASPECT:
                continue
            out.append(
                {
                    "box": [round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5)],
                    "conf": round(float(b.conf[0]), 3),
                    "label": label,
                }
            )
        return out

    # -------------------------------------------------------------- review

    def summary(self, targets: list[str]) -> dict[str, Any]:
        excluded = set(self.data.get("excluded", []))
        per = {}
        for cls in targets:
            total = sum(1 for _ in (self.images / cls).glob("*.jpg"))
            found = self.data["boxes"].get(cls, {})
            usable = sum(1 for n in found if f"{cls}/{n}" not in excluded)
            per[cls] = {"total": total, "found": len(found), "usable": usable}
        weak = [
            f"{cls}/{name}"
            for cls in targets
            for name, hit in sorted(self.data["boxes"].get(cls, {}).items())
            if hit.get("weak")
        ]
        return {
            "classes": per,
            "flagged": self.data.get("flagged", []),
            "excluded": sorted(excluded),
            "weak": weak,
            "label": self.data.get("label"),
        }

    def toggle(self, key: str) -> bool:
        """Exclude or re-include one photo; returns whether it is now excluded."""
        excluded = set(self.data.get("excluded", []))
        now = key not in excluded
        excluded.symmetric_difference_update({key})
        self.data["excluded"] = sorted(excluded)
        self.save()
        return now

    def box_for(self, cls: str, name: str) -> Box | None:
        hit = self.data["boxes"].get(cls, {}).get(name)
        return tuple(hit["box"]) if hit else None  # type: ignore[return-value]

    def thumbnail(self, cls: str, name: str, width: int = 240) -> bytes | None:
        """The photo with its proposed box drawn, for the review grid."""
        frame = cv2.imread(str(self.images / cls / name))
        if frame is None:
            return None
        h, w = frame.shape[:2]
        box = self.box_for(cls, name)
        if box is not None:
            x0, y0, x1, y1 = box
            cv2.rectangle(
                frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (46, 154, 243), 4
            )
        scale = width / float(w)
        small = cv2.resize(frame, (width, int(h * scale)))
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def save(self) -> None:
        self.record.write_text(json.dumps(self.data, indent=1), encoding="utf-8")

    # ------------------------------------------------------------- dataset

    def build(
        self, out: Path, targets: list[str], background: str | None, val_every: int = 5
    ) -> dict:
        """A YOLO detection dataset: reviewed boxes, background as negatives.

        Held-out images never appear in training (the classifier's lesson).
        Returns counts, so the report can say what the model was taught with.
        """
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        for split in ("train", "val"):
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        excluded = set(self.data.get("excluded", []))
        counts: dict[str, int] = {}

        def put(src: Path, cls: str, index: int, line: str | None, i: int) -> None:
            split = "val" if i % val_every == val_every - 1 else "train"
            stem = f"{cls}__{src.stem}"
            shutil.copy(src, out / "images" / split / f"{stem}.jpg")
            (out / "labels" / split / f"{stem}.txt").write_text(line or "", encoding="utf-8")

        for index, cls in enumerate(targets):
            found = self.data["boxes"].get(cls, {})
            usable = [n for n in sorted(found) if f"{cls}/{n}" not in excluded]
            for i, name in enumerate(usable):
                x0, y0, x1, y1 = found[name]["box"]
                line = (
                    f"{index} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {x1 - x0:.6f} {y1 - y0:.6f}\n"
                )
                put(self.images / cls / name, cls, index, line, i)
            counts[cls] = len(usable)
        if background:
            negatives = [
                p
                for p in sorted((self.images / background).glob("*.jpg"))
                if f"{background}/{p.name}" not in excluded
            ]
            for i, p in enumerate(negatives):
                put(p, background, -1, None, i)
            counts[background] = len(negatives)

        (out / "data.yaml").write_text(
            "path: " + str(out.resolve()).replace("\\", "/") + "\n"
            "train: images/train\nval: images/val\n"
            "names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(targets)),
            encoding="utf-8",
        )
        return counts


def image_verdict(model: Any, frame: np.ndarray, min_conf: float) -> dict[str, Any]:
    """Detections on one frame, strongest first, above the floor."""
    result = model.predict(frame, conf=min_conf, verbose=False)[0]
    h, w = frame.shape[:2]

    def area(b: Any) -> float:
        x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
        return round(max(0.0, x1 - x0) * max(0.0, y1 - y0) / float(w * h), 4)

    found = sorted(
        (
            {
                "name": model.names[int(b.cls[0])],
                "conf": round(float(b.conf[0]), 3),
                "area": area(b),
            }
            for b in result.boxes
        ),
        key=lambda d: -d["conf"],
    )
    return {"detections": found}
