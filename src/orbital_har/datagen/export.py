"""Model export for the edge target.

The PS deliverable is a model that runs on an offline standalone system, so the
export path has to be exercised, not assumed. ONNX is the portable baseline and
works everywhere; TensorRT is the Jetson path and can only be built on the
device it will run on - a TensorRT engine is not portable between machines or
even between driver versions, which is exactly the kind of thing that is
discovered the night before a demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FORMATS = ("onnx", "engine", "torchscript", "openvino")


@dataclass
class ExportResult:
    path: Path
    fmt: str
    bytes: int

    def report(self) -> str:
        return f"exported {self.fmt}: {self.path} ({self.bytes / 1e6:.1f} MB)"


def export(
    weights: str,
    fmt: str = "onnx",
    imgsz: int = 640,
    half: bool = False,
    simplify: bool = True,
) -> ExportResult:
    """Export trained weights. Returns where they landed."""
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format {fmt!r}; choose from {FORMATS}")

    from ultralytics import YOLO

    kwargs: dict[str, object] = {"format": fmt, "imgsz": imgsz, "half": half}
    if fmt == "onnx":
        kwargs["simplify"] = simplify

    out = Path(YOLO(weights).export(**kwargs))
    return ExportResult(path=out, fmt=fmt, bytes=out.stat().st_size if out.exists() else 0)
