"""Body actions, read from pose in the operator's own frame of reference.

In orbit there is no floor. "Hand raised" cannot mean "wrist higher in the
image", because an astronaut working inverted raises a hand towards the bottom
of the frame (SIH26174: track the body relative to itself and the rack, not the
floor). So every rule here is measured against the body's own axes:

*Up* is the direction from the hips to the shoulders -- or, when the hips are
out of frame, as they usually are at a desk or a payload rack, from the
shoulders to the head. *Across* runs from the right shoulder to the left.
*Distance* is measured in shoulder widths, so the same rule holds near the
camera and far from it.

Rotate the whole person by any angle and every gesture reads the same. The
tests check exactly that.

Two kinds of action are read:

*Postures* hold in a single frame -- a raised hand, arms crossed.
*Motions* need a short history -- a wave is a raised hand moving side to side,
a lift is a hand rising. They are read from the last couple of seconds of
wrist positions in body coordinates, so walking around while waving is still
a wave, and a laptop at 3-4 frames a second can still see a slow, deliberate
one.

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

from orbital_har.core.types import Gesture
from orbital_har.perception.pose import (
    L_ELBOW,
    L_HIP,
    L_KNEE,
    L_SHOULDER,
    L_WRIST,
    NOSE,
    R_ELBOW,
    R_HIP,
    R_KNEE,
    R_SHOULDER,
    R_WRIST,
    PersonPose,
)

#: Keypoints below this confidence are treated as unseen, never guessed.
MIN_KP_CONF = 0.35

#: Thresholds, in shoulder widths along the body's own axes. Calibrated on real
#: webcam frames: a raised, waving hand keeps its wrist about level with the
#: shoulder while the forearm points straight up, so "raised" is read from the
#: forearm's direction, not from how high the wrist is.
RAISED_FOREARM = 0.7  # forearm within ~45 degrees of the body's up axis
RAISED_MIN_HEIGHT = -0.35  # and the wrist no lower than this below its shoulder
RAISED_NO_ELBOW = 0.6  # without an elbow, the wrist must clear the shoulder by this
FACE_RADIUS = 0.65  # wrist this close to the nose: at the face or mouth
HANDS_TOGETHER = 0.55  # wrists this close to each other
REACH_LENGTH = 1.05  # shoulder-to-wrist span of an extended arm
REACH_ELBOW_DEG = 145.0  # and a nearly straight elbow
#: An arm hanging at the side is long and straight too. Reaching means lifted
#: away from hanging: at least ~50 degrees from the body's own "down".
REACH_FROM_DOWN_COS = 0.64

#: Hand on the head: elbow lifted above the shoulder, wrist beside the head
#: (not in front of the mouth, which is drinking), forearm pointing inwards.
HEAD_ELBOW_UP = 0.15
HEAD_RADIUS = 1.0
HEAD_SIDE_OFFSET = 0.3
#: Arms crossed: each wrist this far over the body's midline, at chest height.
CROSS_OVER = 0.15
CROSS_TOP, CROSS_BOTTOM = 0.3, -2.0
#: Arms out (a T): wrists far out to the sides, about level with the shoulders.
OUT_REACH = 1.25
OUT_LEVEL = 0.6
#: Hands on hips: wrists near their hips, elbows out to the sides -- and the
#: knees in view. Seated at a desk the pose model guesses hips under the table
#: edge, and hands resting on the desk then look exactly like hands on hips;
#: on 170 desk photos no knee was ever seen above 0.1 confidence.
HIP_RADIUS = 0.6
HIP_ELBOW_OUT = 0.7
KNEE_MIN_CONF = 0.5

#: Motions look back this far. Long enough for a slow wave at 3 frames a second.
HISTORY_S = 2.5
#: A person unseen for longer than this starts a fresh history.
HISTORY_GAP_S = 0.8
LIFT = 0.6  # a wrist rising (or falling) this far in the window
LIFT_WINDOW_S = 2.0
WAVE_SWING = 0.25  # side-to-side travel between turns of a wave
WAVE_TURNS = 2
WAVE_MIN_HEIGHT = -0.3  # the waving hand is up, around shoulder height or above
CLAP_OPEN, CLAP_SHUT = 0.8, 0.45  # wrist distance apart / together
CLAPS = 2

Point = tuple[float, float]


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _norm(a: Point) -> float:
    return math.hypot(a[0], a[1])


def _unit(a: Point) -> Point | None:
    n = _norm(a)
    return None if n < 1e-6 else (a[0] / n, a[1] / n)


def _angle_deg(a: Point, vertex: Point, c: Point) -> float:
    u, v = _sub(a, vertex), _sub(c, vertex)
    denom = _norm(u) * _norm(v)
    if denom < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, _dot(u, v) / denom))
    return math.degrees(math.acos(cos))


class GestureReader:
    """Turns one person's keypoints into named body actions.

    Keeps a short history of wrist positions, in body coordinates, for the
    motions. One reader per camera: the history belongs to the person in view.
    """

    def __init__(self) -> None:
        #: (t, left wrist, right wrist, confidence per side), body coordinates.
        self._history: deque[tuple[float, Point | None, Point | None, dict[str, float]]] = deque()

    def read(self, person: PersonPose | None, t: float | None = None) -> tuple[Gesture, ...]:
        now = time.monotonic() if t is None else t
        if person is None:
            return ()

        def kp(index: int) -> tuple[Point, float] | None:
            k = person.get(index, MIN_KP_CONF)
            return None if k is None else ((k.x, k.y), k.conf)

        ls, rs = kp(L_SHOULDER), kp(R_SHOULDER)
        if ls is None or rs is None:
            return ()  # no scale and no axis: say nothing rather than guess
        scale = _norm(_sub(rs[0], ls[0]))
        if scale < 10.0:
            return ()
        mid_shoulder = ((ls[0][0] + rs[0][0]) / 2, (ls[0][1] + rs[0][1]) / 2)

        l_hip, r_hip, nose = kp(L_HIP), kp(R_HIP), kp(NOSE)
        up = self._up(l_hip, r_hip, nose, mid_shoulder)
        if up is None:
            return ()
        # Across: right shoulder to left, made square to "up".
        raw = _sub(ls[0], rs[0])
        across = _unit(_sub(raw, (up[0] * _dot(raw, up), up[1] * _dot(raw, up))))
        if across is None:
            return ()

        def body(p: Point) -> Point:
            """Image point -> (across, up) in shoulder widths from mid-shoulder."""
            d = _sub(p, mid_shoulder)
            return (_dot(d, across) / scale, _dot(d, up) / scale)

        out: list[Gesture] = []
        raised: dict[str, float] = {}
        wrists: dict[str, tuple[Point, float]] = {}
        elbows: dict[str, tuple[Point, float]] = {}
        nose_b = body(nose[0]) if nose is not None else None

        for side, sign, shoulder, elbow_i, wrist_i in (
            ("left", 1.0, ls, L_ELBOW, L_WRIST),
            ("right", -1.0, rs, R_ELBOW, R_WRIST),
        ):
            wrist = kp(wrist_i)
            if wrist is None:
                continue
            elbow = kp(elbow_i)
            (w, wc), (sh, sc) = wrist, shoulder
            wb = body(w)
            wrists[side] = (wb, wc)
            eb = body(elbow[0]) if elbow is not None else None
            if elbow is not None and eb is not None:
                elbows[side] = (eb, elbow[1])

            at_head = False
            if nose_b is not None and eb is not None:
                forearm = _unit(_sub(wb, eb))
                at_head = (
                    forearm is not None
                    and eb[1] > HEAD_ELBOW_UP
                    and wb[1] > 0.2
                    and _norm(_sub(wb, nose_b)) < HEAD_RADIUS
                    and abs(wb[0] - nose_b[0]) >= HEAD_SIDE_OFFSET
                    and forearm[1] < RAISED_FOREARM
                    and forearm[0] * sign < 0  # pointing in, towards the head
                )
            if at_head:
                conf = round(min(wc, elbow[1], nose[1]), 3)  # type: ignore[index]
                out.append(Gesture("hand_on_head", side, conf))

            at_face = False
            if not at_head and nose_b is not None and _norm(_sub(wb, nose_b)) < FACE_RADIUS:
                at_face = True
                out.append(Gesture("hand_to_face", side, round(min(wc, nose[1]), 3)))  # type: ignore[index]

            # Height and direction along the body's own axis, not the image's.
            height = wb[1] - body(sh)[1]
            if eb is not None:
                forearm = _unit(_sub(wb, eb))
                is_up = (
                    forearm is not None
                    and forearm[1] > RAISED_FOREARM
                    and height > RAISED_MIN_HEIGHT
                )
                parts = (wc, sc, elbow[1])  # type: ignore[index]
            else:
                is_up = height > RAISED_NO_ELBOW
                parts = (wc, sc)
            if not at_face and not at_head and is_up:
                conf = round(min(parts), 3)
                raised[side] = conf
                out.append(Gesture("hand_raised", side, conf))

            arm = _unit(_sub(w, sh))
            if (
                elbow is not None
                and arm is not None
                and _norm(_sub(w, sh)) > REACH_LENGTH * scale
                and _angle_deg(sh, elbow[0], w) > REACH_ELBOW_DEG
                and -_dot(arm, up) < REACH_FROM_DOWN_COS
            ):
                out.append(Gesture("reaching", side, round(min(wc, sc, elbow[1]), 3)))

        if len(raised) == 2:
            out.append(Gesture("both_hands_raised", "both", min(raised.values())))

        if len(wrists) == 2:
            (lw, lc), (rw, rc) = wrists["left"], wrists["right"]
            both = round(min(lc, rc), 3)
            crossed = (
                lw[0] < -CROSS_OVER
                and rw[0] > CROSS_OVER
                and CROSS_BOTTOM < lw[1] < CROSS_TOP
                and CROSS_BOTTOM < rw[1] < CROSS_TOP
            )
            if crossed:
                out.append(Gesture("arms_crossed", "both", both))
            elif _norm(_sub(lw, rw)) < HANDS_TOGETHER:
                out.append(Gesture("hands_together", "both", both))

            straight = all(
                s not in elbows
                or _angle_deg(body(ls[0] if s == "left" else rs[0]), elbows[s][0], wrists[s][0])
                > REACH_ELBOW_DEG
                for s in ("left", "right")
            )
            if (
                lw[0] > OUT_REACH
                and rw[0] < -OUT_REACH
                and abs(lw[1]) < OUT_LEVEL
                and abs(rw[1]) < OUT_LEVEL
                and straight
            ):
                out.append(Gesture("arms_out", "both", both))

            knees = person.get(L_KNEE, KNEE_MIN_CONF) and person.get(R_KNEE, KNEE_MIN_CONF)
            if l_hip is not None and r_hip is not None and len(elbows) == 2 and knees:
                lh, rh = body(l_hip[0]), body(r_hip[0])
                if (
                    _norm(_sub(lw, lh)) < HIP_RADIUS
                    and _norm(_sub(rw, rh)) < HIP_RADIUS
                    and elbows["left"][0][0] > HIP_ELBOW_OUT
                    and elbows["right"][0][0] < -HIP_ELBOW_OUT
                ):
                    out.append(Gesture("hands_on_hips", "both", both))

        out += self._motions(now, wrists)
        return tuple(out)

    # ----------------------------------------------------------------- motions

    def _motions(self, now: float, wrists: dict[str, tuple[Point, float]]) -> list[Gesture]:
        """Waving, lifting, lowering and clapping, from the recent wrist history."""
        if self._history and now - self._history[-1][0] > HISTORY_GAP_S:
            self._history.clear()
        confs = {s: c for s, (_, c) in wrists.items()}
        self._history.append(
            (
                now,
                wrists["left"][0] if "left" in wrists else None,
                wrists["right"][0] if "right" in wrists else None,
                confs,
            )
        )
        while self._history and now - self._history[0][0] > HISTORY_S:
            self._history.popleft()

        out: list[Gesture] = []
        for side, idx in (("left", 1), ("right", 2)):
            if side not in wrists:
                continue
            conf = round(wrists[side][1], 3)
            track = [(h[0], h[idx]) for h in self._history if h[idx] is not None]
            recent = [p for t, p in track if now - t <= LIFT_WINDOW_S]
            if len(recent) >= 2:
                y_now = recent[-1][1]
                earlier = [p[1] for p in recent[:-1]]
                if y_now - min(earlier) >= LIFT:
                    out.append(Gesture("lifting", side, conf))
                elif max(earlier) - y_now >= LIFT:
                    out.append(Gesture("lowering", side, conf))
            if self._waving([p for _, p in track]):
                out.append(Gesture("waving", side, conf))

        if "left" in wrists and "right" in wrists:
            gaps = [
                _norm(_sub(h[1], h[2]))
                for h in self._history
                if h[1] is not None and h[2] is not None
            ]
            if self._claps(gaps) >= CLAPS:
                confs_both = min(wrists["left"][1], wrists["right"][1])
                out.append(Gesture("clapping", "both", round(confs_both, 3)))
        return out

    @staticmethod
    def _waving(track: list[Point]) -> bool:
        """A raised hand going side to side, turning at least twice."""
        if len(track) < 4 or track[-1][1] < WAVE_MIN_HEIGHT:
            return False
        xs = [p[0] for p in track if p[1] >= WAVE_MIN_HEIGHT]
        if len(xs) < 4:
            return False
        # A zigzag: a turn counts only after a full swing back, so jitter and
        # a single sweep across are not a wave.
        turns, direction, hi, lo = 0, 0, xs[0], xs[0]
        for x in xs[1:]:
            if direction == 0:
                if x - lo >= WAVE_SWING:
                    direction, hi = 1, x
                elif hi - x >= WAVE_SWING:
                    direction, lo = -1, x
                else:
                    hi, lo = max(hi, x), min(lo, x)
            elif direction == 1:
                if x > hi:
                    hi = x
                elif hi - x >= WAVE_SWING:
                    turns, direction, lo = turns + 1, -1, x
            else:
                if x < lo:
                    lo = x
                elif x - lo >= WAVE_SWING:
                    turns, direction, hi = turns + 1, 1, x
        return turns >= WAVE_TURNS

    @staticmethod
    def _claps(gaps: list[float]) -> int:
        """How many times the hands closed after being apart."""
        count, apart = 0, False
        for d in gaps:
            if d > CLAP_OPEN:
                apart = True
            elif d < CLAP_SHUT and apart:
                count += 1
                apart = False
        return count

    @staticmethod
    def _up(
        l_hip: tuple[Point, float] | None,
        r_hip: tuple[Point, float] | None,
        nose: tuple[Point, float] | None,
        mid_shoulder: Point,
    ) -> Point | None:
        """The body's own "up": hips to shoulders, else shoulders to head."""
        if l_hip is not None and r_hip is not None:
            mid_hip = ((l_hip[0][0] + r_hip[0][0]) / 2, (l_hip[0][1] + r_hip[0][1]) / 2)
            axis = _unit(_sub(mid_shoulder, mid_hip))
            if axis is not None:
                return axis
        if nose is not None:
            return _unit(_sub(nose[0], mid_shoulder))
        return None

    @staticmethod
    def to_payload(frame_id: int, gestures: tuple[Gesture, ...]) -> dict[str, Any]:
        return {
            "frame_id": frame_id,
            "gestures": [{"name": g.name, "side": g.side, "conf": g.conf} for g in gestures],
        }
