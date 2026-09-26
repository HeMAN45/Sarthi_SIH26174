"""Predicate evaluation.

Every evaluator is a pure function of (predicate, context). No I/O, no
mutation, no model calls. That is what lets the entire reasoning layer be built
and regression-tested before perception exists -- and what makes a failing
golden-replay case reproducible.

Latching is deliberately NOT handled here; it is per-step-activation state and
belongs to the engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from orbital_har.reasoning.schema import Procedure, predicate_label
from orbital_har.reasoning.window import FrameSnapshot, Window


@dataclass(frozen=True, slots=True)
class PredicateResult:
    satisfied: bool
    confidence: float
    detail: str

    @property
    def label_value(self) -> str:
        return f"{self.detail}@{self.confidence:.2f}"


@dataclass
class EvalContext:
    window: Window
    procedure: Procedure
    #: When the step under evaluation became active. ``moved`` measures from here.
    step_started_t: float | None = None


UNSATISFIED = PredicateResult(False, 0.0, "no data")


def evaluate(predicate, ctx: EvalContext) -> PredicateResult:
    handler = _HANDLERS.get(predicate.kind)
    if handler is None:  # pragma: no cover - schema prevents this
        raise ValueError(f"no evaluator for predicate kind '{predicate.kind}'")
    return handler(predicate, ctx)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _streak(ctx: EvalContext, n: int) -> list[FrameSnapshot] | None:
    """The last ``n`` snapshots, or None if history is too short."""
    frames = ctx.window.last(n)
    return frames if len(frames) == n else None


def _centroid(
    snap: FrameSnapshot, object_id: str, procedure: Procedure
) -> tuple[float, float, float] | None:
    """Rack-frame centroid of a procedure object in one snapshot."""
    det = snap.best(procedure.classes_for(object_id))
    return det.centroid_mm if det else None


def _target_pos(
    snap: FrameSnapshot, target: str, procedure: Procedure
) -> tuple[float, float, float] | None:
    """Resolve a ``near`` target, which may be a marker or another object."""
    pos = snap.marker_pos(target)
    if pos is not None:
        return pos
    return _centroid(snap, target, procedure)


def _dist_mm(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a, b)


def contact_class(snap: FrameSnapshot, contact) -> str | None:
    """Class of the contacted object, from the record or the frame's tracks."""
    if contact.b_cls:
        return contact.b_cls
    if contact.b_track_id is not None:
        det = snap.track(contact.b_track_id)
        if det:
            return det.cls
    return None


# --------------------------------------------------------------------------
# Evaluators
# --------------------------------------------------------------------------


def _eval_detect(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        latest = ctx.window.latest
        conf = 0.0
        if latest:
            det = latest.best({p.obj_class})
            conf = det.conf if det else 0.0
        return PredicateResult(False, conf, f"{p.obj_class} warming up")

    confs = []
    for f in frames:
        det = f.best({p.obj_class}, p.min_conf, p.min_area)
        if det is None:
            latest = frames[-1].best({p.obj_class})
            if latest is not None and frames[-1].area(latest) < p.min_area:
                return PredicateResult(False, latest.conf, f"{p.obj_class} too far away")
            return PredicateResult(False, latest.conf if latest else 0.0, f"{p.obj_class} not held")
        confs.append(det.conf)
    return PredicateResult(True, min(confs), p.obj_class)


def _eval_absent(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        return PredicateResult(False, 0.0, f"{p.obj_class} warming up")

    worst = 0.0
    for f in frames:
        det = f.best({p.obj_class}, p.min_conf)
        if det is not None:
            return PredicateResult(False, 0.0, f"{p.obj_class} still present")
        seen = f.best({p.obj_class})
        worst = max(worst, seen.conf if seen else 0.0)
    return PredicateResult(True, 1.0 - worst, f"{p.obj_class} absent")


def _eval_contact(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        return PredicateResult(False, 0.0, f"{p.a}-{p.b} warming up")

    # Both sides resolve through the object vocabulary. Perception reports
    # participants by detector class ("red_box_open"); a procedure names them by
    # object id ("red_box"). Resolving both ends lets the two meet without
    # perception ever needing to know what a procedure object is.
    a_classes = ctx.procedure.classes_for(p.a) or {p.a}
    b_classes = ctx.procedure.classes_for(p.b) or {p.b}
    hand = "" if p.side == "any" else f" ({p.side} hand)"
    confs = []
    for f in frames:
        hit = None
        for c in f.contacts:
            if c.a not in a_classes or c.conf < p.min_conf:
                continue
            if p.side != "any" and c.side != p.side:
                continue
            cls = contact_class(f, c)
            if cls is not None and cls in b_classes:
                hit = c
                break
        if hit is None:
            return PredicateResult(False, 0.0, f"{p.a}-{p.b} not in contact{hand}")
        confs.append(hit.conf)
    return PredicateResult(True, min(confs), f"{p.a}-{p.b}{hand}")


def _eval_moved(p, ctx: EvalContext) -> PredicateResult:
    if ctx.step_started_t is None:
        return PredicateResult(False, 0.0, f"{p.obj} no step origin")

    frames = ctx.window.since(ctx.step_started_t)
    if p.min_frac is not None:
        return _moved_in_picture(p, frames, ctx.procedure)
    positions = [pos for f in frames if (pos := _centroid(f, p.obj, ctx.procedure)) is not None]
    if len(positions) < 2:
        return PredicateResult(False, 0.0, f"{p.obj} no rack position")

    displacement = max(_dist_mm(positions[0], q) for q in positions[1:])
    ratio = min(1.0, displacement / p.min_disp_mm)
    return PredicateResult(
        displacement >= p.min_disp_mm, ratio, f"{p.obj} moved {displacement:.0f}mm"
    )


def _moved_in_picture(p, frames: list[FrameSnapshot], procedure: Procedure) -> PredicateResult:
    """How far the object's box centre travelled, in fractions of the frame."""
    classes = procedure.classes_for(p.obj)
    positions = []
    for f in frames:
        det = f.best(classes)
        if det is not None:
            cx, cy = det.bbox_center
            positions.append((cx / max(f.width, 1), cy / max(f.height, 1)))
    if len(positions) < 2:
        return PredicateResult(False, 0.0, f"{p.obj} not seen moving")
    travelled = max(math.dist(positions[0], q) for q in positions[1:])
    ratio = min(1.0, travelled / p.min_frac)
    return PredicateResult(
        travelled >= p.min_frac, ratio, f"{p.obj} moved {travelled * 100:.0f}% of frame"
    )


def _eval_tilted(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        return PredicateResult(False, 0.0, f"{p.obj} warming up")
    classes = ctx.procedure.classes_for(p.obj)
    confs = []
    for f in frames:
        det = f.best(classes, p.min_conf)
        if det is None:
            return PredicateResult(False, 0.0, f"{p.obj} not seen")
        x0, y0, x1, y1 = det.bbox
        ratio = (x1 - x0) / max(y1 - y0, 1e-6)
        if ratio < p.min_ratio:
            return PredicateResult(False, det.conf, f"{p.obj} upright")
        confs.append(det.conf)
    return PredicateResult(True, min(confs), f"{p.obj} tilted")


def _eval_near(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        return PredicateResult(False, 0.0, f"{p.obj}->{p.to} warming up")

    worst_conf = 1.0
    last_dist: float | None = None
    for f in frames:
        src = _centroid(f, p.obj, ctx.procedure)
        dst = _target_pos(f, p.to, ctx.procedure)
        if src is None or dst is None:
            # Rack not locked or object unseen: degrade, never assert.
            return PredicateResult(False, 0.0, f"{p.obj}->{p.to} no rack position")
        d = _dist_mm(src, dst)
        last_dist = d
        if d > p.max_mm:
            return PredicateResult(False, 0.0, f"{p.obj}->{p.to} {d:.0f}mm")
        det = f.best(ctx.procedure.classes_for(p.obj))
        worst_conf = min(worst_conf, det.conf if det else 0.0)

    return PredicateResult(True, worst_conf, f"{p.obj}->{p.to} {last_dist:.0f}mm")


def _eval_dwell(p, ctx: EvalContext) -> PredicateResult:
    region = ctx.procedure.region(p.region)
    x0, y0, x1, y1 = region.rect
    classes = ctx.procedure.classes_for(p.obj)

    frames = list(ctx.window.last(len(ctx.window)))
    if not frames:
        return PredicateResult(False, 0.0, f"{p.obj} no data")

    # Walk backwards while the object stays inside the region.
    held_from: float | None = None
    worst_conf = 1.0
    for f in reversed(frames):
        det = f.best(classes)
        if det is None:
            break
        cx, cy = det.bbox_center
        nx, ny = cx / max(f.width, 1), cy / max(f.height, 1)
        if not (x0 <= nx <= x1 and y0 <= ny <= y1):
            break
        held_from = f.t
        worst_conf = min(worst_conf, det.conf)

    if held_from is None:
        return PredicateResult(False, 0.0, f"{p.obj} outside {p.region}")

    held = frames[-1].t - held_from
    ratio = min(1.0, held / p.seconds)
    return PredicateResult(
        held >= p.seconds,
        worst_conf if held >= p.seconds else ratio,
        f"{p.obj} held {held:.1f}s",
    )


def _eval_count(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        return PredicateResult(False, 0.0, f"count {p.obj_class} warming up")

    seen = 0
    for f in frames:
        seen = f.count(p.obj_class, p.min_conf)
        if seen != p.n:
            return PredicateResult(False, 0.0, f"count {p.obj_class}={seen} want {p.n}")
    return PredicateResult(True, 1.0, f"count {p.obj_class}={seen}")


def _eval_gesture(p, ctx: EvalContext) -> PredicateResult:
    frames = _streak(ctx, p.hold_frames)
    if frames is None:
        return PredicateResult(False, 0.0, f"{p.gesture} warming up")

    confs = []
    for f in frames:
        hit = f.gesture(p.gesture, p.side, p.min_conf)
        if hit is None:
            latest = frames[-1].gesture(p.gesture, p.side)
            return PredicateResult(False, latest.conf if latest else 0.0, f"{p.gesture} not held")
        confs.append(hit.conf)
    return PredicateResult(True, min(confs), p.gesture)


_HANDLERS = {
    "detect": _eval_detect,
    "absent": _eval_absent,
    "contact": _eval_contact,
    "moved": _eval_moved,
    "near": _eval_near,
    "dwell": _eval_dwell,
    "count": _eval_count,
    "gesture": _eval_gesture,
    "tilted": _eval_tilted,
}


def describe(predicate, result: PredicateResult) -> str:
    """Evidence string used in telemetry and the ops evidence panel."""
    mark = "ok" if result.satisfied else "no"
    return f"{predicate_label(predicate)}={mark}:{result.detail}@{result.confidence:.2f}"
