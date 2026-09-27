"""The detector vocabulary, derived from the procedures themselves.

The procedures are the contract. Hardcoding a class list here would create a
second source of truth that silently drifts from the YAML the engine actually
loads - and the failure mode is the worst kind: a model trained on classes the
procedure never asks for, discovered at step seven in front of a jury.

Object *states* are distinct classes (``red_box_open``, not ``red_box`` plus a
state head). That is CLAUDE.md invariant #8 and it is what keeps the perception
stack single-stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orbital_har.reasoning.schema import Procedure

DEFAULT_PROCEDURE_DIR = Path("procedures")

#: Procedures that describe real BAS props. The demo procedures use COCO
#: stand-ins and must never contribute to the trained vocabulary.
BAS_PROCEDURES = ("proc_a", "proc_b")

#: Prompts for open-vocabulary pre-labelling. Derived names like "red box open"
#: read badly to a text-conditioned detector; these are written for it.
_PROMPTS: dict[str, str] = {
    "outer_box_closed": "a closed cardboard storage container",
    "outer_box_open": "an open cardboard storage container with the lid raised",
    "red_box_closed": "a small closed red box",
    "red_box_open": "a small open red box",
    "yellow_box_closed": "a small closed yellow box",
    "yellow_box_open": "a small open yellow box",
    "sample_vial": "a small glass sample vial",
    "tweezers": "a pair of metal tweezers",
    "tether_clip": "a small metal carabiner clip",
}


@dataclass(frozen=True)
class ClassInfo:
    """One detector class and what the procedures expect of it."""

    name: str
    index: int
    object_id: str
    #: Procedure ids that need this class at all.
    procedures: tuple[str, ...]
    tether_required: bool
    prompt: str

    @property
    def is_state(self) -> bool:
        """True when this class encodes an object state rather than an object."""
        return self.name.rsplit("_", 1)[-1] in {"open", "closed"}


def describe(class_name: str) -> str:
    """A prompt an open-vocabulary detector can actually use."""
    if class_name in _PROMPTS:
        return _PROMPTS[class_name]
    return class_name.replace("_", " ")


def load_procedures(
    names: tuple[str, ...] = BAS_PROCEDURES, directory: Path = DEFAULT_PROCEDURE_DIR
) -> list[Procedure]:
    out = []
    for name in names:
        path = Path(name) if Path(name).exists() else directory / f"{name}.yaml"
        out.append(Procedure.load(path))
    return out


def build(
    names: tuple[str, ...] = BAS_PROCEDURES, directory: Path = DEFAULT_PROCEDURE_DIR
) -> list[ClassInfo]:
    """The full detector vocabulary, ordered and indexed for YOLO.

    Order is alphabetical and therefore stable: a class index that shifts
    between training runs silently relabels an entire dataset.
    """
    procedures = load_procedures(names, directory)

    owners: dict[str, str] = {}
    tethered: dict[str, bool] = {}
    used_by: dict[str, set[str]] = {}
    for proc in procedures:
        for obj in proc.objects:
            for cls in obj.classes:
                owners.setdefault(cls, obj.id)
                tethered[cls] = tethered.get(cls, False) or obj.tether_required
                used_by.setdefault(cls, set()).add(proc.procedure.id)

    return [
        ClassInfo(
            name=name,
            index=i,
            object_id=owners[name],
            procedures=tuple(sorted(used_by[name])),
            tether_required=tethered[name],
            prompt=describe(name),
        )
        for i, name in enumerate(sorted(owners))
    ]


def class_names(
    names: tuple[str, ...] = BAS_PROCEDURES, directory: Path = DEFAULT_PROCEDURE_DIR
) -> list[str]:
    return [c.name for c in build(names, directory)]
