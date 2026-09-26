"""On-device dataset capture and classifier training.

Image *classification*, not detection, is what lets an operator teach the system
a new object by pointing a camera at it — no bounding boxes to draw. It is also
how object *states* are captured: ``open_book`` and ``closed_book`` are simply
two classes.

The ``background`` class is mandatory and enforced in :meth:`TrainManager.can_train`.
A softmax always returns one of its classes, so without a negative class a
covered lens yields a confident wrong answer and completes steps on its own.

Mandatory is not the same as sufficient. The classifier answers "which of my
classes does this whole picture look most like", so ``background`` must cover
everything the camera will see that is *not* a target — the empty scene, but
also empty hands, fingers, faces and other objects. A background of blank walls
teaches "not a blank wall means the object", and then a raised finger completes
the step. :meth:`TrainManager.advice` says so before training, and the test
stage (:meth:`TrainManager.predict`) lets the operator see it happen and feed
the mistakes back as training data.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from orbital_har.perception.detect import BACKGROUND_CLASS, classify_verdict, is_background

#: Images per class below which a classifier rarely generalises across angle,
#: distance and light. Three is the hard floor; this is the advice.
RECOMMENDED_PER_CLASS = 20

#: Every n-th image of a class is held out for validation. Spread through the
#: capture order rather than taken from the front, where a burst of near
#: identical frames would make validation a copy of training.
_VAL_EVERY = 5


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "class"


class TrainManager:
    """Manages a per-class image dataset and trains a YOLO image classifier."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.images = root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.model_path: str | None = None
        self.status: dict[str, Any] = {"state": "idle", "message": "", "epoch": 0, "epochs": 0}
        self._test_model: Any = None
        self._test_path: str | None = None
        self._restore()

    # ---------------------------------------------------------------- record

    @property
    def _record(self) -> Path:
        return self.root / "model.json"

    def _restore(self) -> None:
        """Pick up the last trained model, so a restart does not cost a retrain.

        Training takes minutes on a laptop CPU; forgetting the result every time
        the server restarts made the operator pay for it again.
        """
        try:
            rec = json.loads(self._record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        model = rec.get("model")
        if model and Path(model).is_file():
            self.model_path = model
            self.status = {
                "state": "done",
                "message": f"trained {rec.get('trained_at', 'earlier')}",
                "epoch": rec.get("epochs", 0),
                "epochs": rec.get("epochs", 0),
                "model": model,
                "report": rec.get("report"),
            }

    # --------------------------------------------------------------- dataset

    def list_classes(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(p for p in self.images.iterdir() if p.is_dir()):
            n = sum(1 for _ in d.glob("*.jpg"))
            out.append({"name": d.name, "count": n, "background": is_background(d.name)})
        return out

    def add_class(self, name: str) -> str:
        s = slug(name)
        (self.images / s).mkdir(parents=True, exist_ok=True)
        return s

    def prepare(self, names: list[str]) -> list[str]:
        """Create every class a procedure needs, plus the background class."""
        wanted = [*names, BACKGROUND_CLASS]
        have = {c["name"] for c in self.list_classes()}
        return [self.add_class(n) for n in wanted if slug(n) not in have]

    def delete_class(self, name: str) -> None:
        d = self.images / slug(name)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    def add_images(self, name: str, blobs: list[bytes]) -> int:
        d = self.images / self.add_class(name)
        # Continue after the highest existing index, not the count: a deleted
        # file would otherwise make the next capture overwrite a real one.
        taken = [int(p.stem) for p in d.glob("*.jpg") if p.stem.isdigit()]
        start = max(taken) + 1 if taken else 0
        saved = 0
        for blob in blobs:
            arr = np.frombuffer(blob, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            cv2.imwrite(str(d / f"{start + saved:04d}.jpg"), img)
            saved += 1
        return saved

    def add_video(self, name: str, blob: bytes, max_frames: int = 40) -> int:
        """Harvest evenly-spaced frames from a recorded clip into a class.

        Recording a short clip and sampling it is the cheapest way to get the
        variety — angles, lighting, motion blur — that a classifier needs.
        """
        tmp = self.root / f"_upload_{uuid.uuid4().hex[:8]}.mp4"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        saved = 0
        try:
            cap = cv2.VideoCapture(str(tmp))
            if not cap.isOpened():
                return 0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            if total <= 0:  # some containers do not report it
                frames = []
                while len(frames) < max_frames * 6:
                    ok, f = cap.read()
                    if not ok:
                        break
                    frames.append(f)
                step = max(1, len(frames) // max_frames)
                picked = frames[::step][:max_frames]
            else:
                step = max(1, total // max_frames)
                picked = []
                for i in range(0, total, step):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                    ok, f = cap.read()
                    if ok:
                        picked.append(f)
                    if len(picked) >= max_frames:
                        break
            cap.release()
            blobs = []
            for f in picked:
                ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    blobs.append(buf.tobytes())
            saved = self.add_images(name, blobs)
        finally:
            tmp.unlink(missing_ok=True)
        return saved

    # ---------------------------------------------------------------- advice

    def advice(self) -> list[str]:
        """What is likely to go wrong with this dataset, before it does.

        Advisory only: :meth:`can_train` holds the hard rules. These are the
        mistakes that train fine and then fail in front of the camera.
        """
        classes = self.list_classes()
        targets = [c for c in classes if not c["background"]]
        bg = next((c for c in classes if c["background"]), None)
        out: list[str] = []
        if bg is not None and targets:
            most = max(c["count"] for c in targets)
            if bg["count"] < most:
                out.append(
                    f"'{bg['name']}' has {bg['count']} images but '"
                    f"{max(targets, key=lambda c: c['count'])['name']}' has {most}. "
                    "Give background at least as many — it has to cover everything that is "
                    "not a target."
                )
            if len(targets) == 1:
                out.append(
                    f"With one target class the model only learns '{targets[0]['name']}' versus "
                    "background, so anything unlike your background photos will be called "
                    f"'{targets[0]['name']}'. Put your empty hands, fingers, face and other "
                    "objects into background — if your target photos show your hand, background "
                    "must show it too."
                )
        thin = [c["name"] for c in classes if 0 < c["count"] < RECOMMENDED_PER_CLASS]
        if thin:
            out.append(
                f"Under {RECOMMENDED_PER_CLASS} images: {', '.join(thin)}. More variety "
                "(angle, distance, light, which hand) matters more than more epochs."
            )
        return out

    # -------------------------------------------------------------- training

    def can_train(self) -> tuple[bool, str]:
        cls = [c for c in self.list_classes() if c["count"] > 0]
        if len(cls) < 2:
            return False, "need at least 2 classes, each with images"
        if any(c["count"] < 3 for c in cls):
            return False, "each class needs at least 3 images"
        if not any(c["background"] for c in cls):
            return False, (
                f"add a '{BACKGROUND_CLASS}' class with shots of the EMPTY scene "
                "(and a covered lens). Without it the model must guess a real "
                "class even when nothing is there."
            )
        return True, ""

    def train(self, epochs: int = 15) -> tuple[bool, str]:
        if self.status["state"] == "training":
            return False, "already training"
        ok, msg = self.can_train()
        if not ok:
            return False, msg
        threading.Thread(target=self._train, args=(epochs,), daemon=True).start()
        return True, "started"

    def _build_split(self) -> Path:
        """Train/val folders with no image in both.

        Validation that re-uses training images reports near-perfect accuracy
        for a model that has only memorised them — the one number an operator
        would trust was the one that could not be trusted.
        """
        ds = self.root / "dataset"
        if ds.exists():
            shutil.rmtree(ds, ignore_errors=True)
        for cls in self.list_classes():
            if cls["count"] == 0:
                continue
            imgs = sorted((self.images / cls["name"]).glob("*.jpg"))
            val = set(imgs[_VAL_EVERY - 1 :: _VAL_EVERY]) or {imgs[-1]}
            for split in ("train", "val"):
                (ds / split / cls["name"]).mkdir(parents=True, exist_ok=True)
            for img in imgs:
                split = "val" if img in val else "train"
                shutil.copy(img, ds / split / cls["name"] / img.name)
        return ds

    def _train(self, epochs: int) -> None:
        from ultralytics import YOLO

        with self._lock:
            self.status = {
                "state": "training",
                "message": "preparing data",
                "epoch": 0,
                "epochs": epochs,
            }
        try:
            ds = self._build_split()
            model = YOLO("yolo11n-cls.pt")

            def on_epoch(trainer) -> None:
                self.status["epoch"] = int(getattr(trainer, "epoch", 0)) + 1
                self.status["message"] = "training"

            model.add_callback("on_train_epoch_end", on_epoch)
            model.train(
                data=str(ds),
                epochs=epochs,
                imgsz=224,
                verbose=False,
                plots=False,
                # Absolute on purpose: Ultralytics re-roots a relative project
                # under its own global runs_dir, which can belong to another
                # checkout entirely -- the weights then live outside our data.
                project=str((self.root / "runs").resolve()),
                name="cls",
                exist_ok=True,
            )
            best = Path(model.trainer.best)  # authoritative path from Ultralytics
            if not best.exists():
                raise FileNotFoundError(f"trained weights not found at {best}")
            # Still "training" to the UI until the report exists: a model shown
            # as ready before it is scored invites deploying it unscored.
            self.status = {
                "state": "training",
                "message": "scoring on held-out images",
                "epoch": epochs,
                "epochs": epochs,
            }
            report = self.evaluate(best, ds / "val")
            self.model_path = str(best)
            self._record.write_text(
                json.dumps(
                    {
                        "model": str(best),
                        "epochs": epochs,
                        "report": report,
                        "trained_at": time.strftime("%Y-%m-%d %H:%M"),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.status = {
                "state": "done",
                "message": f"trained: {best.name}",
                "epoch": epochs,
                "epochs": epochs,
                "model": str(best),
                "report": report,
            }
        except Exception as exc:  # pragma: no cover - demo aid
            self.status = {"state": "error", "message": str(exc), "epoch": 0, "epochs": epochs}

    @staticmethod
    def evaluate(weights: Path, val_dir: Path) -> dict[str, Any]:
        """Per-class accuracy on held-out images.

        Honest about its limits: held-out photos come from the same capture
        sessions, so they measure memory of those scenes, not robustness to a
        hand the model never saw. The test stage is where that shows.
        """
        from ultralytics import YOLO

        model = YOLO(str(weights))
        per: dict[str, dict[str, int]] = {}
        for cls_dir in sorted(p for p in val_dir.iterdir() if p.is_dir()):
            imgs = sorted(cls_dir.glob("*.jpg"))
            correct = 0
            for img in imgs:
                result = model.predict(str(img), verbose=False)[0]
                if model.names[int(result.probs.top1)] == cls_dir.name:
                    correct += 1
            per[cls_dir.name] = {"correct": correct, "total": len(imgs)}
        total = sum(v["total"] for v in per.values())
        right = sum(v["correct"] for v in per.values())
        return {"accuracy": round(right / total, 3) if total else None, "per_class": per}

    # ------------------------------------------------------------------ test

    @staticmethod
    def _load(path: str) -> Any:
        from ultralytics import YOLO

        return YOLO(path)

    def _model(self) -> Any:
        """The trained model, reloaded whenever training has replaced it.

        Keyed on the file's modification time as well as its path: every
        retrain writes the same ``best.pt``, so a path-only cache went on
        testing the first model long after it had been retrained.
        """
        with self._lock:
            if self.model_path is None:
                return None
            try:
                key = f"{self.model_path}@{Path(self.model_path).stat().st_mtime_ns}"
            except OSError:
                return None
            if self._test_path != key:
                self._test_model = self._load(self.model_path)
                self._test_path = key
            return self._test_model

    def model_classes(self) -> list[str]:
        model = self._model()
        return [] if model is None else [model.names[i] for i in sorted(model.names)]

    def predict(self, blob: bytes) -> dict[str, Any] | None:
        """What the trained model makes of one camera frame, and whether it counts.

        Uses the same acceptance rule as live supervision, so "would complete
        the step" here means it would complete the step in a run.
        """
        model = self._model()
        if model is None:
            return None
        img = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        result = model.predict(img, verbose=False)[0]
        v = classify_verdict(model.names, result.probs.data.tolist())
        return {
            "name": v.name,
            "conf": round(v.conf, 3),
            "margin": round(v.margin, 3),
            "accepted": v.accepted,
            "why": v.why,
            "classes": [{"name": n, "conf": c} for n, c in v.ranked],
        }
