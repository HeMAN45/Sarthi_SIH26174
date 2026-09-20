"""Predicate evaluation.

These run with no camera, no models and no engine -- which is the whole point
of keeping evaluators pure.
"""

from __future__ import annotations

from orbital_har.core.types import Contact, DetectedObject, Marker, RackState
from orbital_har.reasoning.predicates import EvalContext, evaluate
from orbital_har.reasoning.schema import Procedure
from orbital_har.reasoning.window import FrameSnapshot, Window

RACK = RackState(
    found=True,
    rotation_deg=0.0,
    markers=(Marker("R", (250.0, -50.0, 0.0)), Marker("Y", (-250.0, -50.0, 0.0))),
    quality=1.0,
)
NO_RACK = RackState(found=False, rotation_deg=0.0, markers=(), quality=0.0)


def snap(
    i: int,
    objects: list[DetectedObject] | None = None,
    contacts: list[Contact] | None = None,
    rack: RackState = RACK,
) -> FrameSnapshot:
    return FrameSnapshot(
        frame_id=i,
        t=1000.0 + i / 30.0,
        objects=objects or [],
        contacts=contacts or [],
        rack=rack,
    )


def det(cls: str, conf: float = 0.9, pos=(0.0, 0.0, 0.0), bbox=None, track_id=1):
    return DetectedObject(
        cls=cls,
        conf=conf,
        bbox=bbox or (600.0, 320.0, 680.0, 400.0),
        track_id=track_id,
        centroid_mm=pos,
    )


def ctx(proc: Procedure, frames: list[FrameSnapshot], started: float | None = None):
    w = Window()
    for f in frames:
        w.append(f)
    return EvalContext(window=w, procedure=proc, step_started_t=started)


def pred(proc: Procedure, step_id: str, index: int = 0):
    return proc.step(step_id).requires[index]


# ------------------------------------------------------------------- detect


def test_detect_needs_a_full_hold_streak(proc_a: Procedure) -> None:
    p = pred(proc_a, "s1")  # detect outer_box_open, hold_frames 12
    frames = [snap(i, [det("outer_box_open")]) for i in range(11)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is False

    frames.append(snap(11, [det("outer_box_open")]))
    result = evaluate(p, ctx(proc_a, frames))
    assert result.satisfied is True
    assert result.confidence == 0.9


def test_detect_breaks_on_a_single_missing_frame(proc_a: Procedure) -> None:
    p = pred(proc_a, "s1")
    frames = [snap(i, [det("outer_box_open")]) for i in range(20)]
    frames[-3] = snap(17, [])
    assert evaluate(p, ctx(proc_a, frames)).satisfied is False


def test_detect_confidence_is_the_weakest_frame(proc_a: Procedure) -> None:
    p = pred(proc_a, "s1")
    frames = [snap(i, [det("outer_box_open", conf=0.95)]) for i in range(20)]
    frames[-1] = snap(19, [det("outer_box_open", conf=0.61)])
    assert evaluate(p, ctx(proc_a, frames)).confidence == 0.61


def test_detect_below_the_floor_is_not_evidence(proc_a: Procedure) -> None:
    p = pred(proc_a, "s1")  # min_conf defaults to the 0.35 detection floor
    frames = [snap(i, [det("outer_box_open", conf=0.2)]) for i in range(20)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is False


# ------------------------------------------------------------------ contact


def test_contact_resolves_object_id_through_its_classes(proc_a: Procedure) -> None:
    p = pred(proc_a, "s2", 0)  # contact [hand, red_box]
    contacts = [Contact(a="hand", b_track_id=7, b_cls="red_box_closed", conf=0.8)]
    frames = [snap(i, [det("red_box_closed", track_id=7)], contacts) for i in range(6)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is True


def test_contact_resolves_class_from_track_id(proc_a: Procedure) -> None:
    p = pred(proc_a, "s2", 0)
    contacts = [Contact(a="hand", b_track_id=7, b_cls=None, conf=0.8)]
    frames = [snap(i, [det("red_box_open", track_id=7)], contacts) for i in range(6)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is True


def test_contact_with_the_wrong_object_does_not_count(proc_a: Procedure) -> None:
    p = pred(proc_a, "s2", 0)
    contacts = [Contact(a="hand", b_track_id=9, b_cls="yellow_box_closed", conf=0.8)]
    frames = [snap(i, [], contacts) for i in range(6)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is False


# --------------------------------------------------------------------- near


def test_near_is_satisfied_inside_the_radius(proc_a: Procedure) -> None:
    p = pred(proc_a, "s2", 1)  # near red_box -> R, max 120
    frames = [snap(i, [det("red_box_closed", pos=(250.0, -50.0, 0.0))]) for i in range(10)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is True


def test_near_fails_outside_the_radius(proc_a: Procedure) -> None:
    p = pred(proc_a, "s2", 1)
    frames = [snap(i, [det("red_box_closed", pos=(0.0, 400.0, 0.0))]) for i in range(10)]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is False


def test_near_degrades_when_the_rack_is_lost(proc_a: Procedure) -> None:
    """Without a rack lock there are no positions -- abstain, never assert."""
    p = pred(proc_a, "s2", 1)
    frames = [snap(i, [det("red_box_closed", pos=None)], rack=NO_RACK) for i in range(10)]
    result = evaluate(p, ctx(proc_a, frames))
    assert result.satisfied is False
    assert "no rack position" in result.detail


def test_near_can_target_another_object(proc_a: Procedure) -> None:
    p = pred(proc_a, "s5", 1)  # near sample_vial -> yellow_box, max 90
    frames = [
        snap(
            i,
            [
                det("sample_vial", pos=(-250.0, -50.0, 0.0), track_id=1),
                det("yellow_box_closed", pos=(-250.0, -50.0, 0.0), track_id=2),
            ],
        )
        for i in range(10)
    ]
    assert evaluate(p, ctx(proc_a, frames)).satisfied is True


# -------------------------------------------------------------------- dwell


def test_dwell_requires_the_full_duration(proc_b: Procedure) -> None:
    p = pred(proc_b, "s7")  # dwell sample_vial in camera_verify, 2.0 s
    inside = (600.0, 320.0, 680.0, 400.0)  # centre of a 1280x720 frame
    frames = [snap(i, [det("sample_vial", bbox=inside)]) for i in range(30)]  # 1 s
    assert evaluate(p, ctx(proc_b, frames)).satisfied is False

    frames += [snap(30 + i, [det("sample_vial", bbox=inside)]) for i in range(45)]
    assert evaluate(p, ctx(proc_b, frames)).satisfied is True


def test_dwell_resets_when_the_object_leaves_the_region(proc_b: Procedure) -> None:
    p = pred(proc_b, "s7")
    inside = (600.0, 320.0, 680.0, 400.0)
    outside = (100.0, 50.0, 180.0, 130.0)
    frames = [snap(i, [det("sample_vial", bbox=inside)]) for i in range(70)]
    frames.append(snap(70, [det("sample_vial", bbox=outside)]))
    assert evaluate(p, ctx(proc_b, frames)).satisfied is False


# -------------------------------------------------------------------- moved


def test_moved_measures_from_the_step_origin() -> None:
    from orbital_har.reasoning.schema import MovedPredicate

    proc = Procedure.model_validate(
        {
            "procedure": {"id": "t", "name": "T"},
            "objects": [{"id": "box", "classes": ["box_closed"]}],
            "steps": [
                {
                    "id": "s1",
                    "name": "move it",
                    "voice": "move it",
                    "requires": [{"moved": "box", "min_disp_mm": 100}],
                }
            ],
        }
    )
    p = proc.steps[0].requires[0]
    assert isinstance(p, MovedPredicate)

    frames = [snap(i, [det("box_closed", pos=(0.0, 0.0, 0.0))]) for i in range(5)]
    frames += [snap(5 + i, [det("box_closed", pos=(150.0, 0.0, 0.0))]) for i in range(5)]
    assert evaluate(p, ctx(proc, frames, started=1000.0)).satisfied is True

    still = [snap(i, [det("box_closed", pos=(0.0, 0.0, 0.0))]) for i in range(10)]
    assert evaluate(p, ctx(proc, still, started=1000.0)).satisfied is False


def test_moved_without_a_step_origin_abstains() -> None:
    proc = Procedure.model_validate(
        {
            "procedure": {"id": "t", "name": "T"},
            "objects": [{"id": "box", "classes": ["box_closed"]}],
            "steps": [
                {
                    "id": "s1",
                    "name": "move it",
                    "voice": "move it",
                    "requires": [{"moved": "box", "min_disp_mm": 100}],
                }
            ],
        }
    )
    frames = [snap(i, [det("box_closed", pos=(500.0, 0.0, 0.0))]) for i in range(10)]
    assert evaluate(proc.steps[0].requires[0], ctx(proc, frames)).satisfied is False
