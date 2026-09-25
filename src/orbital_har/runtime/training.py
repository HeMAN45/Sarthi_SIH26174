"""On-device dataset capture and classifier training.

Image *classification*, not detection, is what lets an operator teach the system
a new object by pointing a camera at it — no bounding boxes to draw. It is also
how object *states* are captured: ``open_book`` and ``closed_book`` are simply
two classes.

The ``background`` class is mandatory and enforced in :meth:`TrainManager.can_train`.
A softmax always returns one of its classes, so without a negative class a
covered lens yields a confident wrong answer and completes steps on its own.
"""

from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from orbital_har.perception.detect import BACKGROUND_CLASS, is_background


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

    def delete_class(self, name: str) -> None:
        d = self.images / slug(name)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    def add_images(self, name: str, blobs: list[bytes]) -> int:
        d = self.images / self.add_class(name)
        existing = sum(1 for _ in d.glob("*.jpg"))
        saved = 0
        for blob in blobs:
            arr = np.frombuffer(blob, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            cv2.imwrite(str(d / f"{existing + saved:04d}.jpg"), img)
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
        ds = self.root / "dataset"
        if ds.exists():
            shutil.rmtree(ds, ignore_errors=True)
        for cls in self.list_classes():
            if cls["count"] == 0:
                continue
            imgs = sorted((self.images / cls["name"]).glob("*.jpg"))
            n_val = max(1, len(imgs) // 5)
            val = set(imgs[:n_val])
            for split in ("train", "val"):
                (ds / split / cls["name"]).mkdir(parents=True, exist_ok=True)
            for img in imgs:
                # every image goes to train; a fifth also seeds val
                shutil.copy(img, ds / "train" / cls["name"] / img.name)
                if img in val:
                    shutil.copy(img, ds / "val" / cls["name"] / img.name)
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
                project=str(self.root / "runs"),
                name="cls",
                exist_ok=True,
            )
            best = Path(model.trainer.best)  # authoritative path from Ultralytics
            if not best.exists():
                raise FileNotFoundError(f"trained weights not found at {best}")
            self.model_path = str(best)
            self.status = {
                "state": "done",
                "message": f"trained: {best.name}",
                "epoch": epochs,
                "epochs": epochs,
                "model": str(best),
            }
        except Exception as exc:  # pragma: no cover - demo aid
            self.status = {"state": "error", "message": str(exc), "epoch": 0, "epochs": epochs}
