"""Training configuration for the BAS-prop detector.

The augmentation defaults here are the interesting part, and they are not the
Ultralytics defaults.

``degrees=180`` and ``flipud=0.5``. Stock detection training assumes a world
with a floor: rotation is limited to a few degrees and vertical flips are off,
because a ground photograph is never upside down. An astronaut has no fixed
"up", and the payload camera sees them at any angle. Training with full rotation
and vertical flips costs nothing and is the cheapest 80% of the PS's orientation
clause — the remaining 20% is the rack canonicalization in
``perception.rackframe``, which straightens the input before the model ever
sees it.

The weights default to the **small** tier, not nano. Nano is a live-demo
compromise for CPU inference; the deliverable is a trained model, and training
is where the extra capacity is worth paying for. Export decides what runs on the
edge, not this.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TrainConfig:
    data: Path
    weights: str = "yolo11s.pt"
    epochs: int = 100
    imgsz: int = 640
    #: -1 lets Ultralytics pick from available memory.
    batch: int = -1
    device: str | None = None
    project: Path = Path("runs")
    name: str = "bas"
    patience: int = 30
    seed: int = 0

    # --- orientation-agnostic augmentation (see module docstring) ---
    degrees: float = 180.0
    flipud: float = 0.5
    fliplr: float = 0.5
    scale: float = 0.5
    translate: float = 0.1
    #: Props are recognised by colour. Keep hue jitter small or a red box
    #: becomes a yellow one and the two classes blur together.
    hsv_h: float = 0.010
    hsv_s: float = 0.6
    hsv_v: float = 0.4
    mosaic: float = 1.0
    #: Disabled for the last few epochs so the model finishes on real layouts.
    close_mosaic: int = 10

    extra: dict[str, Any] = field(default_factory=dict)

    def to_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``YOLO.train``."""
        skip = {"weights", "extra", "data", "project"}
        kwargs = {k: v for k, v in asdict(self).items() if k not in skip and v is not None}
        kwargs["data"] = str(self.data)
        kwargs["project"] = str(self.project)
        kwargs["exist_ok"] = True
        kwargs["plots"] = True
        kwargs.update(self.extra)
        return kwargs

    def describe(self) -> str:
        return "\n".join(
            [
                f"weights   {self.weights}",
                f"data      {self.data}",
                f"epochs    {self.epochs}   imgsz {self.imgsz}   batch {self.batch}",
                f"rotation  +/-{self.degrees:.0f} deg, flipud {self.flipud}, fliplr {self.fliplr}",
                "            (full rotation + vertical flips: there is no floor in orbit)",
            ]
        )


def train(config: TrainConfig) -> Path:
    """Train and return the path to the best weights."""
    from ultralytics import YOLO

    model = YOLO(config.weights)
    model.train(**config.to_kwargs())
    best = Path(model.trainer.best)
    if not best.exists():
        raise FileNotFoundError(f"trained weights not found at {best}")
    return best
