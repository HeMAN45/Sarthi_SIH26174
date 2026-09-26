"""Body actions read in the body's own frame.

SIH26174 asks for tracking that does not assume a floor: an astronaut raising a
hand while upside down is still raising a hand. Every test below builds one
posture and then rotates the whole body, checking the reading never changes.
"""

from __future__ import annotations

import math

import pytest

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.perception.gestures import GestureReader
from orbital_har.perception.pose import Keypoint, PersonPose
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure

# COCO-17 indices
NOSE, LS, RS, LE, RE, LW, RW, LH, RH = 0, 5, 6, 7, 8, 9, 10, 11, 12

#: A person facing the camera, 100 px shoulder width, image "up" is -y.
BASE = {
    NOSE: (250, 120),
    LS: (300, 200),
    RS: (200, 200),
    LH: (290, 400),
    RH: (210, 400),
    # arms hanging down by default
    LE: (320, 280),
    LW: (322, 360),
    RE: (180, 280),
    RW: (178, 360),
}


def person(
    points: dict[int, tuple[float, float]], angle: float = 0.0, hips: bool = True
) -> PersonPose:
    """A PersonPose from named points, the whole body rotated by ``angle``."""
    cx, cy = 250.0, 250.0
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    kps = []
    for i in range(17):
        if i in points and (hips or i not in (LH, RH)):
            x, y = points[i]
            x, y = x - cx, y - cy
            kps.append(Keypoint(cx + x * c - y * s, cy + x * s + y * c, 0.9))
        else:
            kps.append(Keypoint(0.0, 0.0, 0.0))
    return PersonPose(keypoints=tuple(kps), conf=0.9)


def names(points, angle=0.0, hips=True) -> set[str]:
    return {f"{g.name}:{g.side}" for g in GestureReader().read(person(points, angle, hips))}


ANGLES = [0.0, 90.0, 180.0, 237.0]


@pytest.mark.parametrize("angle", ANGLES)
@pytest.mark.parametrize("hips", [True, False])
class TestOrientationFree:
    def test_arms_down_is_no_gesture(self, angle, hips) -> None:
        assert names(BASE, angle, hips) == set()

    def test_a_raised_hand_reads_raised_at_any_angle(self, angle, hips) -> None:
        # forearm straight up, wrist about level with the shoulder: a wave
        pts = BASE | {LE: (330, 200), LW: (332, 110)}
        assert names(pts, angle, hips) == {"hand_raised:left"}

    def test_both_hands_up(self, angle, hips) -> None:
        pts = BASE | {LE: (330, 200), LW: (332, 110), RE: (170, 200), RW: (168, 110)}
        assert names(pts, angle, hips) == {
            "hand_raised:left",
            "hand_raised:right",
            "both_hands_raised:both",
        }

    def test_hand_at_the_mouth_is_not_a_raised_hand(self, angle, hips) -> None:
        pts = BASE | {RE: (215, 190), RW: (240, 140)}  # drinking
        assert names(pts, angle, hips) == {"hand_to_face:right"}

    def test_hands_together(self, angle, hips) -> None:
        pts = BASE | {LE: (290, 290), LW: (256, 300), RE: (210, 290), RW: (244, 300)}
        assert names(pts, angle, hips) == {"hands_together:both"}

    def test_reaching_out(self, angle, hips) -> None:
        pts = BASE | {LE: (360, 202), LW: (425, 204)}
        assert names(pts, angle, hips) == {"reaching:left"}

    def test_hand_on_head(self, angle, hips) -> None:
        # elbow lifted out to the side, wrist beside the head, forearm pointing in
        pts = BASE | {LE: (340, 150), LW: (295, 110)}
        assert names(pts, angle, hips) == {"hand_on_head:left"}

    def test_arms_crossed(self, angle, hips) -> None:
        pts = BASE | {LE: (280, 280), LW: (215, 270), RE: (220, 280), RW: (285, 270)}
        assert names(pts, angle, hips) == {"arms_crossed:both"}

    def test_arms_out_in_a_t(self, angle, hips) -> None:
        pts = BASE | {LE: (350, 200), LW: (420, 200), RE: (150, 200), RW: (80, 200)}
        assert names(pts, angle, hips) == {"arms_out:both", "reaching:left", "reaching:right"}

    def test_hands_on_hips_needs_the_hips_and_the_knees(self, angle, hips) -> None:
        pts = BASE | {LE: (345, 300), LW: (285, 380), RE: (155, 300), RW: (215, 380)}
        standing = pts | {13: (288, 520), 14: (212, 520)}
        assert names(standing, angle, hips) == ({"hands_on_hips:both"} if hips else set())
        # At a desk the knees are under the table: hands resting there are not on hips.
        assert names(pts, angle, hips) == set()


# ----------------------------------------------------------------- motions


def motion(frames, angle=0.0, dt=0.3) -> set[str]:
    """Feed postures one after another; what is read on the last one."""
    reader = GestureReader()
    got: tuple = ()
    for i, pts in enumerate(frames):
        got = reader.read(person(pts, angle), t=i * dt)
    return {f"{g.name}:{g.side}" for g in got}


RAISED_L = BASE | {LE: (330, 200), LW: (332, 110)}


def _wave(xs):
    return [RAISED_L | {LW: (x, 110)} for x in xs]


@pytest.mark.parametrize("angle", ANGLES)
def test_a_wave_is_a_raised_hand_going_side_to_side(angle) -> None:
    assert "waving:left" in motion(_wave([332, 372, 332, 292, 332, 372]), angle)


def test_one_sweep_is_not_a_wave_and_jitter_is_not_either() -> None:
    assert "waving:left" not in motion(_wave([332, 372, 332]))
    assert "waving:left" not in motion(_wave([332, 336, 330, 335, 331, 334]))


def test_a_wave_needs_the_hand_up() -> None:
    low = [BASE | {LE: (320, 280), LW: (x, 360)} for x in (322, 362, 322, 282, 322, 362)]
    assert not any(n.startswith("waving") for n in motion(low))


@pytest.mark.parametrize("angle", ANGLES)
def test_lifting_and_lowering_a_hand(angle) -> None:
    hang, mid, up = BASE, BASE | {LE: (325, 240), LW: (326, 240)}, RAISED_L
    assert "lifting:left" in motion([hang, mid, up], angle)
    assert "lowering:left" in motion([up, mid, hang], angle)
    assert not {"lifting:left", "lowering:left"} & motion([up, up, up], angle)


@pytest.mark.parametrize("angle", ANGLES)
def test_clapping_is_hands_closing_again_and_again(angle) -> None:
    apart = BASE | {LE: (320, 290), LW: (330, 300), RE: (180, 290), RW: (170, 300)}
    shut = BASE | {LE: (290, 290), LW: (256, 300), RE: (210, 290), RW: (244, 300)}
    assert "clapping:both" in motion([apart, shut, apart, shut], angle)
    assert "clapping:both" not in motion([apart, shut], angle)  # once is hands together


def test_a_gap_in_sight_starts_the_history_again() -> None:
    reader = GestureReader()
    for i, x in enumerate([332, 372, 332]):
        reader.read(person(RAISED_L | {LW: (x, 110)}), t=i * 0.3)
    # Two seconds unseen, then the rest of the wave: not enough of it is recent.
    got = set()
    for i, x in enumerate([292, 332, 372]):
        got = {g.name for g in reader.read(person(RAISED_L | {LW: (x, 110)}), t=3.0 + i * 0.3)}
    assert "waving" not in got


def test_no_shoulders_means_no_reading_rather_than_a_guess() -> None:
    pts = {k: v for k, v in BASE.items() if k not in (LS, RS)}
    assert GestureReader().read(person(pts)) == ()


def test_unseen_keypoints_are_not_used() -> None:
    p = person(BASE | {LE: (330, 200), LW: (332, 110)})
    kps = list(p.keypoints)
    kps[LW] = Keypoint(kps[LW].x, kps[LW].y, 0.1)  # the raised wrist, barely seen
    assert GestureReader().read(PersonPose(tuple(kps), 0.9)) == ()


# ------------------------------------------------------------------ engine


def _proc(extra: str = "") -> Procedure:
    import yaml

    return Procedure.model_validate(
        yaml.safe_load(
            f"""
procedure: {{id: g, name: Gesture test}}
objects: [{{id: bottle, classes: [bottle]}}]
steps:
  - id: s1
    name: Raise your left hand
    voice: Raise your left hand.
    requires: [{{gesture: hand_raised, side: left, hold_frames: 5}}]
  - id: s2
    name: Drink
    voice: Drink.
    preconditions: [s1]
    requires:
      - gesture: hand_to_face
        hold_frames: 5
      - detect: bottle
        hold_frames: 1
{extra}
"""
        )
    )


def _feed(engine: Engine, frames: int, start: int, gestures=(), objects=()) -> None:
    for i in range(frames):
        fid = start + i
        t = fid / 15.0
        engine.on_event(
            Event(
                t, fid * 10, "capture", EventType.FRAME.value, {"frame_id": fid, "w": 640, "h": 480}
            )
        )
        engine.on_event(
            Event(
                t,
                fid * 10 + 1,
                "detect",
                EventType.DETECTION.value,
                {"frame_id": fid, "objects": list(objects)},
            )
        )
        if gestures:
            engine.on_event(
                Event(
                    t,
                    fid * 10 + 2,
                    "gestures",
                    EventType.GESTURE.value,
                    {"frame_id": fid, "gestures": list(gestures)},
                )
            )


def test_a_gesture_step_completes_only_on_the_right_side() -> None:
    engine = Engine(
        _proc(), EngineConfig(tau_complete=0.6, tau_abstain=0.35, completion_lookahead=0)
    )
    _feed(engine, 12, 1, gestures=[{"name": "hand_raised", "side": "right", "conf": 0.9}])
    assert engine.state_of("s1") != StepState.COMPLETE
    _feed(engine, 12, 20, gestures=[{"name": "hand_raised", "side": "left", "conf": 0.9}])
    assert engine.state_of("s1") == StepState.COMPLETE


def test_drinking_needs_the_gesture_and_the_bottle() -> None:
    engine = Engine(
        _proc(), EngineConfig(tau_complete=0.6, tau_abstain=0.35, completion_lookahead=0)
    )
    _feed(engine, 10, 1, gestures=[{"name": "hand_raised", "side": "left", "conf": 0.9}])
    face = [{"name": "hand_to_face", "side": "right", "conf": 0.9}]
    _feed(engine, 12, 20, gestures=face)  # scratching your face is not drinking
    assert engine.state_of("s2") != StepState.COMPLETE
    bottle = [{"cls": "bottle", "conf": 0.9, "bbox": [0, 0, 50, 90], "track_id": None}]
    _feed(engine, 12, 40, gestures=face, objects=bottle)
    assert engine.state_of("s2") == StepState.COMPLETE


def test_an_unknown_gesture_is_refused_at_load_with_its_name() -> None:
    with pytest.raises(ValueError, match="unknown gesture 'jazz_hands'"):
        _proc(
            """  - id: s3
    name: X
    voice: X.
    requires: [{gesture: jazz_hands}]"""
        )
