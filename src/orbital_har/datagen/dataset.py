"""YOLO dataset layout, splitting and readiness reporting.

One decision here is worth more than the rest of the module: **the split is by
clip, not by frame.** Footage of a procedure is recorded in takes, and adjacent
frames of a take are near-identical. Splitting randomly puts a frame in train
and its neighbour in val, and val mAP climbs to a number that means nothing —
the model is being asked to recognise images it has effectively seen. Grouping
by clip is the difference between an honest metric and a flattering one.

Layout (Ultralytics detection):

    root/
      data.yaml
      raw/           inbox: images + YOLO .txt labels, any name
      images/train/  images/val/
      labels/train/  labels/val/
"""

from __future__ import annotations

import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
SPLITS = ("train", "val")

#: Per-class instance count below which a class is considered under-sampled.
#: Chosen from the usual small-object-detection guidance, not from theory —
#: report the number and let the operator decide.
MIN_INSTANCES_PER_CLASS = 300

#: Frames from one take share this prefix, e.g. ``take03_0147.jpg`` -> ``take03``.
_CLIP_RE = re.compile(r"^(.*?)[-_]?\d+$")


def clip_of(stem: str) -> str:
    """The take a frame belongs to. Frames without a number are their own clip."""
    match = _CLIP_RE.match(stem)
    return match.group(1) or stem if match and match.group(1) else stem


@dataclass
class DatasetStats:
    classes: list[str]
    images: dict[str, int] = field(default_factory=dict)
    instances: dict[str, int] = field(default_factory=dict)
    clips: dict[str, int] = field(default_factory=dict)
    unlabelled: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def total_images(self) -> int:
        return sum(self.images.values())

    @property
    def total_instances(self) -> int:
        return sum(self.instances.values())

    @property
    def ready(self) -> bool:
        return not self.issues

    def report(self) -> str:
        lines = [
            f"{self.total_images} images, {self.total_instances} instances",
            "  " + "  ".join(f"{k}={v}" for k, v in sorted(self.images.items())),
            "",
        ]
        width = max((len(c) for c in self.classes), default=10)
        for cls in self.classes:
            n = self.instances.get(cls, 0)
            flag = "" if n >= MIN_INSTANCES_PER_CLASS else "  <- under-sampled"
            lines.append(f"  {cls:<{width}}  {n:>5}{flag}")
        if self.unlabelled:
            lines += ["", f"  {len(self.unlabelled)} image(s) with no label file"]
        if self.issues:
            lines += ["", "Not ready:"] + [f"  - {i}" for i in self.issues]
        else:
            lines += ["", "Ready to train."]
        return "\n".join(lines)


def scaffold(root: Path, classes: list[str]) -> Path:
    """Create the directory layout and data.yaml. Safe to re-run."""
    for split in SPLITS:
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir(parents=True, exist_ok=True)
    return write_data_yaml(root, classes)


def write_data_yaml(root: Path, classes: list[str]) -> Path:
    """Ultralytics dataset descriptor. Class order is the contract."""
    path = root / "data.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "path": str(root.resolve()),
                "train": "images/train",
                "val": "images/val",
                "names": dict(enumerate(classes)),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _images_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def label_path_for(image: Path, root: Path, split: str) -> Path:
    return root / "labels" / split / f"{image.stem}.txt"


def split_dataset(
    root: Path, val_frac: float = 0.2, seed: int = 0, move: bool = True
) -> dict[str, int]:
    """Distribute ``raw/`` into train and val, keeping each clip intact.

    Returns the per-split image counts. Re-running is safe: existing splits are
    cleared first so a second run cannot leave a frame in both.
    """
    raw = root / "raw"
    images = _images_in(raw)
    if not images:
        return {"train": 0, "val": 0}

    by_clip: dict[str, list[Path]] = {}
    for image in images:
        by_clip.setdefault(clip_of(image.stem), []).append(image)

    clips = sorted(by_clip)
    random.Random(seed).shuffle(clips)
    n_val = max(1, round(len(clips) * val_frac)) if len(clips) > 1 else 0
    val_clips = set(clips[:n_val])

    for split in SPLITS:
        for directory in (root / "images" / split, root / "labels" / split):
            if directory.is_dir():
                shutil.rmtree(directory)
            directory.mkdir(parents=True, exist_ok=True)

    counts = {"train": 0, "val": 0}
    transfer = shutil.move if move else shutil.copy
    for clip, members in by_clip.items():
        split = "val" if clip in val_clips else "train"
        for image in members:
            transfer(str(image), str(root / "images" / split / image.name))
            label = raw / f"{image.stem}.txt"
            if label.exists():
                transfer(str(label), str(root / "labels" / split / label.name))
            counts[split] += 1
    return counts


def stats(root: Path, classes: list[str]) -> DatasetStats:
    """Count what is actually on disk and judge whether it is trainable."""
    out = DatasetStats(classes=list(classes))
    out.instances = dict.fromkeys(classes, 0)

    clips: dict[str, set[str]] = {s: set() for s in SPLITS}
    for split in SPLITS:
        images = _images_in(root / "images" / split)
        out.images[split] = len(images)
        for image in images:
            clips[split].add(clip_of(image.stem))
            label = label_path_for(image, root, split)
            if not label.exists():
                out.unlabelled.append(str(image))
                continue
            for line in label.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if not parts:
                    continue
                try:
                    index = int(parts[0])
                except ValueError:
                    out.issues.append(f"{label.name}: non-numeric class index {parts[0]!r}")
                    continue
                if 0 <= index < len(classes):
                    out.instances[classes[index]] += 1
                else:
                    out.issues.append(f"{label.name}: class index {index} is out of range")
    out.clips = {s: len(c) for s, c in clips.items()}

    if out.total_images == 0:
        out.issues.append("no images - record footage and put it in raw/, then split")
        return out
    if out.images.get("val", 0) == 0:
        out.issues.append("no validation images - run the split")
    if overlap := clips["train"] & clips["val"]:
        out.issues.append(
            f"{len(overlap)} clip(s) appear in BOTH train and val: {sorted(overlap)[:3]} "
            "- val mAP would be inflated by near-duplicate frames"
        )
    missing = [c for c in classes if out.instances.get(c, 0) == 0]
    if missing:
        out.issues.append(f"{len(missing)} class(es) with no instances at all: {missing}")
    if out.unlabelled:
        out.issues.append(f"{len(out.unlabelled)} image(s) have no label file")
    return out
