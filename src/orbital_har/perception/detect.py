"""Object detection and the on-device state classifier.

Two detection modes share one interface:

*Boxes.* A YOLO detector (or YOLO-World, open-vocabulary) yields bounding boxes.
This is the path the trained BAS-prop model will take. A detector trained on
this device runs *beside* the stock one, so the operator's own objects join
the 80 stock ones instead of replacing them.

*Classification.* A whole presented object is classified instead - which is what
makes "just add pictures" training work, and how object *states* (open vs closed)
are captured without any bounding boxes.

The classifier path needs one guard that the box path does not. A softmax always
returns *some* class: it can never say "nothing here". Without a negative class a
covered lens produces a confident wrong answer and completes steps. Hence the
mandatory ``background`` class, plus a confidence **and** margin gate - the
50/50 predictions seen on junk input are exactly the ones to throw away.

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

#: The "nothing is being presented" escape hatch. Required for classifiers.
BACKGROUND_CLASS = "background"

#: Other names people reach for when they mean "nothing is being presented".
_BG_ALIASES = {"none", "nothing", "empty", "no_object", "noobject", "idle", "blank"}

#: Accept a classification only when it is confident AND clearly ahead of the
#: runner-up.
CLS_MIN_CONF = 0.60
CLS_MIN_MARGIN = 0.15

#: Box-detector confidence floor. Distinct from the engine's decision
#: thresholds; see CLAUDE.md invariant #12.
BOX_MIN_CONF = 0.35

COCO80 = sorted(
    [
        "person",
        "bicycle",
        "car",
        "motorcycle",
        "airplane",
        "bus",
        "train",
        "truck",
        "boat",
        "traffic light",
        "fire hydrant",
        "stop sign",
        "parking meter",
        "bench",
        "bird",
        "cat",
        "dog",
        "horse",
        "sheep",
        "cow",
        "elephant",
        "bear",
        "zebra",
        "giraffe",
        "backpack",
        "umbrella",
        "handbag",
        "tie",
        "suitcase",
        "frisbee",
        "skis",
        "snowboard",
        "sports ball",
        "kite",
        "baseball bat",
        "baseball glove",
        "skateboard",
        "surfboard",
        "tennis racket",
        "bottle",
        "wine glass",
        "cup",
        "fork",
        "knife",
        "spoon",
        "bowl",
        "banana",
        "apple",
        "sandwich",
        "orange",
        "broccoli",
        "carrot",
        "hot dog",
        "pizza",
        "donut",
        "cake",
        "chair",
        "couch",
        "potted plant",
        "bed",
        "dining table",
        "toilet",
        "tv",
        "laptop",
        "mouse",
        "remote",
        "keyboard",
        "cell phone",
        "microwave",
        "oven",
        "toaster",
        "sink",
        "refrigerator",
        "book",
        "clock",
        "vase",
        "scissors",
        "teddy bear",
        "hair drier",
        "toothbrush",
    ]
)


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_background(name: str) -> bool:
    """True for the negative class, tolerating typos like 'backgraound'.

    Getting this wrong silently breaks the whole model - the class stops acting
    as the "nothing here" escape hatch - so match generously. No real object
    name lands within two edits of 'background'.
    """
    n = name.strip().lower().replace(" ", "_").replace("-", "_")
    return n in _BG_ALIASES or _edit_distance(n, BACKGROUND_CLASS) <= 2


@dataclass(frozen=True)
class ClassVerdict:
    """What the classifier said about one frame, and whether it counts."""

    name: str
    conf: float
    margin: float
    accepted: bool
    #: Why it was ignored; empty when accepted.
    why: str
    #: Every class with its probability, most likely first.
    ranked: tuple[tuple[str, float], ...]


def classify_verdict(names: dict[int, str], probs: list[float]) -> ClassVerdict:
    """The one rule for accepting a classification.

    Shared by live supervision and the Models page's test stage, so what the
    operator sees while testing is exactly what a run would conclude.
    """
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    ranked = tuple((names[i], round(float(probs[i]), 4)) for i in order)
    name, conf = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = conf - second

    if is_background(name):
        why = "nothing presented"
    elif conf < CLS_MIN_CONF:
        why = f"low confidence {conf:.2f}"
    elif margin < CLS_MIN_MARGIN:
        why = f"ambiguous (margin {margin:.2f})"
    else:
        why = ""
    return ClassVerdict(name, conf, margin, not why, why, ranked)


@dataclass
class Candidate:
    """One detection considered this frame, kept or not."""

    cls: str
    conf: float
    bbox: tuple[float, float, float, float]
    area_frac: float
    kept: bool = False


@dataclass
class DetectionResult:
    #: Detection payload dicts, ready for a ``detection`` event.
    objects: list[dict[str, Any]] = field(default_factory=list)
    #: Everything seen, for the overlay - including what was rejected and why.
    candidates: list[Candidate] = field(default_factory=list)
    #: Classifier-mode commentary, e.g. "ignored: ambiguous (margin 0.04)".
    note: str = ""
    #: True when the classifier's answer was rejected rather than accepted.
    rejected: bool = False


class Detector:
    """Wraps an Ultralytics model and normalises its output."""

    def __init__(self, model: Any, open_vocab: bool = False) -> None:
        self.model = model
        self.open_vocab = open_vocab
        self.classifier: Any = None
        #: A detector trained on this device, run beside the stock model.
        self.trained_model: Any = None
        self.trained_path: str | None = None
        #: Trained classes the operator deleted. Weights cannot forget a class
        #: until retrained, so it is hidden and never reported instead.
        self.retired: set[str] = set()
        self.names = model.names
        #: What the stock model can find.
        self.base_classes = COCO80 if open_vocab else sorted(set(model.names.values()))
        self.all_classes = list(self.base_classes)

    # ------------------------------------------------------------------ modes

    @property
    def trained(self) -> bool:
        return self.trained_model is not None

    @property
    def trained_classes(self) -> list[str]:
        """The operator's own objects -- what the trained detector finds."""
        if self.trained_model is None:
            return []
        return sorted(set(self.trained_model.names.values()) - self.retired)

    def retire(self, names: set[str]) -> None:
        """Stop reporting trained classes the operator has deleted."""
        self.retired |= set(names)
        self.all_classes = sorted(set(self.base_classes) | set(self.trained_classes))

    @property
    def mode(self) -> str:
        if self.classifier is not None:
            return "classifier"
        if self.trained:
            return "detector"
        return "open-vocab" if self.open_vocab else "stand-in"

    def set_classes(self, classes: set[str]) -> None:
        """Tell an open-vocabulary model which objects to look for."""
        if not self.open_vocab:
            return
        self.model.set_classes(sorted(classes) or ["object"])
        self.names = self.model.names

    def use_detector(self, model_path: str) -> list[str]:
        """Add a detector trained on this device beside the stock one.

        The operator's objects join the 80 instead of replacing them, so one
        procedure can ask for the stock bottle and a trained cap state. Where a
        trained class shares a stock name, the trained model answers for it.
        Leaves classifier mode. Returns the trained classes.
        """
        from ultralytics import YOLO

        model = YOLO(model_path)
        self.trained_model = model
        self.trained_path = model_path
        self.retired = set()  # a new model knows only what it was trained on
        self.classifier = None
        self.all_classes = sorted(set(self.base_classes) | set(self.trained_classes))
        return [model.names[i] for i in sorted(model.names)]

    def use_classifier(self, model_path: str) -> list[str]:
        """Swap to classification mode. Returns its non-background classes."""
        from ultralytics import YOLO

        model = YOLO(model_path)
        self.classifier = model
        self.open_vocab = False
        names = [model.names[i] for i in sorted(model.names) if not is_background(model.names[i])]
        self.all_classes = sorted(set(names))
        return names

    # ----------------------------------------------------------------- detect

    def detect(
        self,
        frame: np.ndarray,
        wanted: set[str],
        min_area: float = 0.06,
        multi: bool = False,
    ) -> DetectionResult:
        """One frame in, detection payloads out.

        ``multi`` reports every object in view, which a scene procedure needs:
        ``near`` compares two of them, ``dwell`` watches one sitting in its
        spot. Without it the procedure is a presentation -- "show the bottle"
        -- and only objects held up close count: at least ``min_area`` of the
        frame. Every such object counts, so two can be shown at once.
        """
        if self.classifier is not None:
            return self._classify(frame)
        return self._boxes(frame, wanted, min_area, multi)

    def _classify(self, frame: np.ndarray) -> DetectionResult:
        result = self.classifier.predict(frame, verbose=False)[0]
        v = classify_verdict(self.classifier.names, result.probs.data.tolist())

        out = DetectionResult(
            note=f"{v.name}  {v.conf:.2f}  (margin {v.margin:.2f})", rejected=not v.accepted
        )
        if not v.accepted:
            out.note += f"\nignored: {v.why}"
            return out

        h, w = frame.shape[:2]
        out.objects.append(
            {
                "cls": v.name,
                "conf": round(v.conf, 3),
                "bbox": [0.0, 0.0, float(w), float(h)],
                "track_id": None,
            }
        )
        return out

    def _boxes(
        self, frame: np.ndarray, wanted: set[str], min_area: float, multi: bool
    ) -> DetectionResult:
        # Each model is asked only for what the procedure wants, and a model
        # with nothing wanted from it is not run at all: a body-actions-only
        # procedure costs no detection.
        mine = set(self.trained_classes) & wanted
        cands = self._candidates(self.model, self.names, frame, wanted - mine)
        if mine:
            cands += self._candidates(self.trained_model, self.trained_model.names, frame, mine)
        cands.sort(key=lambda c: c.area_frac, reverse=True)

        out = DetectionResult(candidates=cands)
        for cand in cands:
            if not multi and cand.area_frac < min_area:
                continue
            cand.kept = True
            out.objects.append(
                {
                    "cls": cand.cls,
                    "conf": round(cand.conf, 3),
                    "bbox": list(cand.bbox),
                    "track_id": None,
                }
            )
        return out

    @staticmethod
    def _candidates(
        model: Any, names: dict[int, str], frame: np.ndarray, wanted: set[str]
    ) -> list[Candidate]:
        if not wanted:
            return []
        result = model.predict(frame, conf=BOX_MIN_CONF, verbose=False)[0]
        frame_area = float(frame.shape[0] * frame.shape[1])
        cands: list[Candidate] = []
        for box in result.boxes:
            cls_name = names[int(box.cls[0])]
            if cls_name not in wanted:
                continue
            x0, y0, x1, y1 = (float(v) for v in box.xyxy[0])
            area = max(0.0, x1 - x0) * max(0.0, y1 - y0) / frame_area
            cands.append(
                Candidate(
                    cls=cls_name, conf=float(box.conf[0]), bbox=(x0, y0, x1, y1), area_frac=area
                )
            )
        return cands

    # ---------------------------------------------------------------- overlay

    @staticmethod
    def draw(frame: np.ndarray, result: DetectionResult, *, mirror: bool = False) -> None:
        """Annotate in place. Rejected detections are shown, not hidden.

        With ``mirror`` the frame is already flipped; boxes move with it, while
        their labels are written normally so they stay readable.
        """
        width = frame.shape[1]
        if result.note:
            colour = (0, 170, 255) if result.rejected else (0, 220, 0)
            cv2.rectangle(frame, (6, 6), (frame.shape[1] - 6, frame.shape[0] - 6), colour, 3)
            for i, line in enumerate(result.note.split("\n")):
                cv2.putText(
                    frame,
                    line,
                    (18, 40 + i * 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8 if i == 0 else 0.6,
                    colour,
                    2,
                )
            return

        for i, cand in enumerate(result.candidates):
            colour = (0, 220, 0) if cand.kept else (110, 110, 110)
            x0, y0, x1, y1 = (int(v) for v in cand.bbox)
            if mirror:
                x0, x1 = width - 1 - x1, width - 1 - x0
            cv2.rectangle(frame, (x0, y0), (x1, y1), colour, 3 if cand.kept else 1)
            tag = f"{cand.cls} {cand.conf:.2f} {cand.area_frac * 100:.0f}%"
            if not cand.kept and i == 0:
                tag += " (hold closer)"
            cv2.putText(frame, tag, (x0, y0 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2)
