"""Procedure definition schema and loader.

A procedure is data. The engine never changes when a procedure does -- that is
invariant #5 in CLAUDE.md and the basis of differentiator D-02.

Validation is strict and fails loudly with a field path: per FR-11 the system
must refuse to start on an invalid procedure rather than discover the problem
at step seven in front of a jury.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

PREDICATE_KEYS = frozenset({"detect", "absent", "contact", "moved", "near", "dwell", "count"})

#: Which model field the compact YAML form's value maps onto.
_ARG_FIELD = {
    "detect": "obj_class",
    "absent": "obj_class",
    "count": "obj_class",
    "moved": "obj",
    "near": "obj",
    "dwell": "obj",
}


class ProcedureError(ValueError):
    """Raised when a procedure file is unloadable or invalid."""


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------


#: Detection floor, not a decision threshold. A predicate is "satisfied" once
#: the evidence exists at all; whether that evidence is good enough to act on is
#: the engine's call via tau_complete / tau_abstain. Setting this above
#: tau_abstain would make the abstention path unreachable -- a predicate could
#: never be both satisfied and too weak to trust.
DETECTION_FLOOR = 0.35


class _BasePredicate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: When true, once satisfied during a step activation the predicate stays
    #: satisfied for the rest of that step. Needed for "the operator did pick
    #: this up at some point", where the contact is long over by the time the
    #: rest of the step completes.
    latch: bool = False


class DetectPredicate(_BasePredicate):
    kind: Literal["detect"] = "detect"
    obj_class: str
    min_conf: float = Field(default=DETECTION_FLOOR, ge=0.0, le=1.0)
    hold_frames: int = Field(default=12, ge=1)


class AbsentPredicate(_BasePredicate):
    kind: Literal["absent"] = "absent"
    obj_class: str
    min_conf: float = Field(default=DETECTION_FLOOR, ge=0.0, le=1.0)
    hold_frames: int = Field(default=12, ge=1)


class ContactPredicate(_BasePredicate):
    kind: Literal["contact"] = "contact"
    a: str
    b: str
    min_conf: float = Field(default=DETECTION_FLOOR, ge=0.0, le=1.0)
    hold_frames: int = Field(default=3, ge=1)


class MovedPredicate(_BasePredicate):
    kind: Literal["moved"] = "moved"
    obj: str
    min_disp_mm: float = Field(gt=0.0)


class NearPredicate(_BasePredicate):
    kind: Literal["near"] = "near"
    obj: str
    to: str
    max_mm: float = Field(gt=0.0)
    hold_frames: int = Field(default=6, ge=1)


class DwellPredicate(_BasePredicate):
    kind: Literal["dwell"] = "dwell"
    obj: str
    region: str
    seconds: float = Field(gt=0.0)


class CountPredicate(_BasePredicate):
    kind: Literal["count"] = "count"
    obj_class: str
    n: int = Field(ge=0)
    min_conf: float = Field(default=DETECTION_FLOOR, ge=0.0, le=1.0)
    hold_frames: int = Field(default=12, ge=1)


def _normalize_predicate(value: Any) -> Any:
    """Expand the compact YAML form into an explicitly tagged mapping.

    ``{detect: red_box_open, min_conf: 0.7}`` becomes
    ``{kind: detect, obj_class: red_box_open, min_conf: 0.7}``.
    """
    if not isinstance(value, dict):
        raise ValueError(f"predicate must be a mapping, got {type(value).__name__}")
    if "kind" in value:
        return value

    present = sorted(PREDICATE_KEYS & set(value))
    if len(present) != 1:
        raise ValueError(
            f"predicate needs exactly one of {sorted(PREDICATE_KEYS)}; got keys {sorted(value)}"
        )

    kind = present[0]
    out = {k: v for k, v in value.items() if k != kind}
    out["kind"] = kind
    arg = value[kind]

    if kind == "contact":
        if not isinstance(arg, list | tuple) or len(arg) != 2:
            raise ValueError("contact takes exactly two participants, e.g. [hand, red_box]")
        out["a"], out["b"] = arg[0], arg[1]
    else:
        out[_ARG_FIELD[kind]] = arg
    return out


_PredicateUnion = Annotated[
    DetectPredicate
    | AbsentPredicate
    | ContactPredicate
    | MovedPredicate
    | NearPredicate
    | DwellPredicate
    | CountPredicate,
    Field(discriminator="kind"),
]

Predicate = Annotated[_PredicateUnion, BeforeValidator(_normalize_predicate)]


def predicate_label(p: Any) -> str:
    """Short human-readable form used in evidence lists and telemetry."""
    if p.kind in ("detect", "absent", "count"):
        return f"{p.kind}:{p.obj_class}"
    if p.kind == "contact":
        return f"contact:{p.a},{p.b}"
    if p.kind == "near":
        return f"near:{p.obj}->{p.to}"
    if p.kind == "dwell":
        return f"dwell:{p.obj}@{p.region}"
    return f"{p.kind}:{p.obj}"


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


class ObjectDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    classes: list[str]
    tether_required: bool = False


class RegionDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    #: x0, y0, x1, y1 as fractions of frame width/height.
    rect: tuple[float, float, float, float]

    @model_validator(mode="after")
    def _check_rect(self) -> RegionDef:
        x0, y0, x1, y1 = self.rect
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise ValueError(
                f"region '{self.id}': rect must be 0<=x0<x1<=1 and 0<=y0<y1<=1, got {self.rect}"
            )
        return self


class GroupDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    members: list[str]


class StepDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    voice: str
    preconditions: list[str] = Field(default_factory=list)
    group: str | None = None
    requires: list[Predicate] = Field(default_factory=list)
    any_of: list[Predicate] = Field(default_factory=list)
    timeout_s: int | None = None
    on_timeout: Literal["stall", "skip", "ignore"] = "stall"

    @model_validator(mode="after")
    def _needs_evidence(self) -> StepDef:
        if not self.requires and not self.any_of:
            raise ValueError(
                f"step '{self.id}': needs at least one predicate in 'requires' or 'any_of'"
            )
        return self


class ProcedureMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    version: int = 1
    rack_markers: str = "DICT_4X4_50"
    vocabulary: str = "orbital-v1"


class Procedure(BaseModel):
    """A fully validated, queryable procedure definition."""

    model_config = ConfigDict(extra="forbid")

    procedure: ProcedureMeta
    objects: list[ObjectDef]
    markers: list[str] = Field(default_factory=list)
    regions: list[RegionDef] = Field(default_factory=list)
    groups: list[GroupDef] = Field(default_factory=list)
    steps: list[StepDef]

    # ---------------------------------------------------------------- checks

    @model_validator(mode="after")
    def _cross_validate(self) -> Procedure:
        self._check_unique_ids()
        self._check_preconditions()
        self._check_groups()
        self._check_predicate_references()
        return self

    def _check_unique_ids(self) -> None:
        for label, ids in (
            ("step", [s.id for s in self.steps]),
            ("object", [o.id for o in self.objects]),
            ("region", [r.id for r in self.regions]),
            ("group", [g.id for g in self.groups]),
        ):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                raise ValueError(f"duplicate {label} id(s): {dupes}")
        if not self.steps:
            raise ValueError("procedure has no steps")

    def _check_preconditions(self) -> None:
        known = {s.id for s in self.steps}
        for step in self.steps:
            for pre in step.preconditions:
                if pre == step.id:
                    raise ValueError(f"step '{step.id}': cannot depend on itself")
                if pre not in known:
                    raise ValueError(f"step '{step.id}': precondition '{pre}' is not a known step")
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        deps = {s.id: set(s.preconditions) for s in self.steps}
        resolved: set[str] = set()
        remaining = dict(deps)
        while remaining:
            ready = {sid for sid, pre in remaining.items() if pre <= resolved}
            if not ready:
                raise ValueError(f"precondition cycle among steps: {sorted(remaining)}")
            resolved |= ready
            for sid in ready:
                remaining.pop(sid)

    def _check_groups(self) -> None:
        group_ids = {g.id for g in self.groups}
        step_ids = {s.id for s in self.steps}
        for group in self.groups:
            for member in group.members:
                if member not in step_ids:
                    raise ValueError(f"group '{group.id}': member '{member}' is not a known step")
        for step in self.steps:
            if step.group is not None and step.group not in group_ids:
                raise ValueError(f"step '{step.id}': group '{step.group}' is not declared")

    def _check_predicate_references(self) -> None:
        object_ids = {o.id for o in self.objects}
        region_ids = {r.id for r in self.regions}
        marker_ids = set(self.markers)
        all_classes = {c for o in self.objects for c in o.classes}
        # 'hand' and 'glove' are perception primitives, not procedure objects.
        participants = object_ids | {"hand", "glove"}

        for step in self.steps:
            for p in [*step.requires, *step.any_of]:
                where = f"step '{step.id}' predicate '{predicate_label(p)}'"
                if p.kind in ("detect", "absent", "count"):
                    if p.obj_class not in all_classes:
                        raise ValueError(
                            f"{where}: class '{p.obj_class}' is not declared by any object"
                        )
                elif p.kind == "contact":
                    for side in (p.a, p.b):
                        if side not in participants:
                            raise ValueError(
                                f"{where}: '{side}' is not a declared object or a hand"
                            )
                elif p.kind == "near":
                    if p.obj not in object_ids:
                        raise ValueError(f"{where}: '{p.obj}' is not a declared object")
                    if p.to not in object_ids | marker_ids:
                        raise ValueError(
                            f"{where}: target '{p.to}' is neither a declared object "
                            f"nor a declared marker {sorted(marker_ids)}"
                        )
                elif p.kind == "dwell":
                    if p.obj not in object_ids:
                        raise ValueError(f"{where}: '{p.obj}' is not a declared object")
                    if p.region not in region_ids:
                        raise ValueError(f"{where}: region '{p.region}' is not declared")
                elif p.kind == "moved" and p.obj not in object_ids:
                    raise ValueError(f"{where}: '{p.obj}' is not a declared object")

    # ------------------------------------------------------------- accessors

    @property
    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]

    @property
    def vocabulary_classes(self) -> set[str]:
        """Detector classes this procedure needs in order to run at all."""
        return {c for o in self.objects for c in o.classes}

    def step(self, step_id: str) -> StepDef:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def ordinal(self, step_id: str) -> int:
        return self.step_ids.index(step_id)

    def classes_for(self, object_id: str) -> set[str]:
        for o in self.objects:
            if o.id == object_id:
                return set(o.classes)
        return set()

    def object_for_class(self, class_name: str) -> str | None:
        for o in self.objects:
            if class_name in o.classes:
                return o.id
        return None

    def group_members(self, step_id: str) -> set[str]:
        step = self.step(step_id)
        if step.group is None:
            return set()
        for g in self.groups:
            if g.id == step.group:
                return set(g.members)
        return set()

    def region(self, region_id: str) -> RegionDef:
        for r in self.regions:
            if r.id == region_id:
                return r
        raise KeyError(region_id)

    def tethered_objects(self) -> set[str]:
        return {o.id for o in self.objects if o.tether_required}

    # ---------------------------------------------------------------- loader

    @classmethod
    def load(cls, path: str | Path) -> Procedure:
        p = Path(path)
        if not p.exists():
            raise ProcedureError(f"procedure file not found: {p}")
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ProcedureError(f"{p}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ProcedureError(f"{p}: expected a mapping at the top level")
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ProcedureError(f"{p}: invalid procedure\n{_format_errors(exc)}") from exc


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def check_vocabulary(procedure: Procedure, available: Iterable[str]) -> set[str]:
    """Return the classes a procedure needs that the loaded detector lacks.

    Empty set means the procedure is runnable. Used by the hot-swap path so the
    system refuses a procedure it cannot perceive instead of failing mid-run.
    """
    return procedure.vocabulary_classes - set(available)
