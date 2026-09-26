"""Experiments built in the dashboard, saved as procedure files.

A saved experiment is an ordinary procedure YAML file (invariant #5: procedures
are configuration, never code) kept in its own folder beside the built-in
library, so an operator's experiments survive restarts and never mix with the
files under version control.

Each step asks for an object, a body action, or both -- "drink" is the bottle
*and* the hand at the face. The file is the whole record: it is read back to
list, run and edit an experiment, so nothing else can fall out of step with it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import yaml

from orbital_har.core.types import GESTURES, ONE_HANDED_GESTURES
from orbital_har.reasoning.schema import Procedure

#: How an experiment step reads aloud when the operator gives no wording.
GESTURE_PHRASE = {
    "hand_raised": "Raise your hand",
    "hand_to_face": "Bring your hand to your face",
    "hand_on_head": "Put your hand on your head",
    "reaching": "Reach out",
    "waving": "Wave your hand",
    "lifting": "Lift your hand",
    "lowering": "Lower your hand",
    "both_hands_raised": "Raise both hands",
    "hands_together": "Bring your hands together",
    "arms_crossed": "Cross your arms",
    "arms_out": "Stretch both arms out to the sides",
    "hands_on_hips": "Put your hands on your hips",
    "clapping": "Clap your hands",
}

#: A motion done with the object itself reads better as the object's verb.
_MOTION_WITH = {"lifting": "Lift", "lowering": "Lower", "waving": "Wave"}

#: What the step does with its object. ``show``: held up to the camera.
#: ``hold``: in a hand. ``pour``: in a hand and tipped over. ``move``: in a
#: hand and carried across the picture.
HOWS = ("show", "hold", "pour", "move")
HANDS = ("any", "left", "right")
_HOW_VERB = {"show": "Present", "hold": "Pick up", "pour": "Pour from", "move": "Move"}

#: About half a second at webcam rates: a one-frame blip cannot complete a step.
HOLD_FRAMES = 8
MAX_STEPS = 15
#: "Held up to the camera" in an experiment that also sees the whole scene.
PRESENT_AREA = 0.06
#: "Moved": carried a quarter of the way across the picture.
MOVE_FRAC = 0.25


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return (s or "experiment")[:48]


def _clean(spec: dict[str, Any]) -> tuple[str | None, str | None, str, str]:
    obj = (spec.get("object") or "").strip() or None
    gesture = (spec.get("gesture") or "").strip() or None
    hand = (spec.get("hand") or "any").strip() or "any"
    how = (spec.get("how") or "show").strip() or "show"
    return obj, gesture, hand, how


def _with_hand(phrase: str, hand: str) -> str:
    if hand == "any":
        return phrase
    if "your hand" in phrase:
        return phrase.replace("your hand", f"your {hand} hand")
    return f"{phrase} with your {hand} hand"


def step_name(spec: dict[str, Any]) -> str:
    """The wording for one step: the operator's own, or a sensible default."""
    text = (spec.get("instruction") or "").strip()
    if text:
        return text
    obj, gesture, hand, how = _clean(spec)
    if obj and gesture:
        if how == "show":
            return f"{_with_hand(GESTURE_PHRASE[gesture], hand)} with the {obj}"
        if gesture in _MOTION_WITH:
            return _with_hand(f"{_MOTION_WITH[gesture]} the {obj}", hand)
        return f"{_with_hand(GESTURE_PHRASE[gesture], hand)} holding the {obj}"
    if gesture:
        return _with_hand(GESTURE_PHRASE[gesture], hand)
    if how == "show":
        return f"Present the {obj}"
    return _with_hand(f"{_HOW_VERB[how]} the {obj}", hand)


def procedure_dict(
    steps: list[dict[str, Any]], name: str, proc_id: str, vocabulary: str = "custom"
) -> dict[str, Any]:
    """The procedure, as the plain mapping its YAML file holds.

    ``steps`` are ``{object?, gesture?, instruction?, hand?, how?}``: an
    object, a body action, or both, optionally with the hand that must do it.
    Raises ValueError with the step number when a step is unusable.
    """
    if not steps:
        raise ValueError("an experiment needs at least one step")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"keep an experiment to {MAX_STEPS} steps or fewer")

    # Holding, pouring or moving an object is a scene: perception then reports
    # everything in view, so "show" steps carry their own "held up close" floor.
    scene = any(_clean(spec)[3] != "show" and _clean(spec)[0] for spec in steps)
    classes: list[str] = []
    out_steps: list[dict[str, Any]] = []
    for i, spec in enumerate(steps, start=1):
        obj, gesture, hand, how = _clean(spec)
        if not obj and not gesture:
            raise ValueError(f"step {i} needs an object, a body action, or both")
        if gesture and gesture not in GESTURES:
            raise ValueError(f"step {i}: unknown body action '{gesture}'")
        if hand not in HANDS:
            raise ValueError(f"step {i}: hand must be one of {', '.join(HANDS)}")
        if how not in HOWS:
            raise ValueError(f"step {i}: unknown way to use an object '{how}'")
        if how != "show" and not obj:
            raise ValueError(f"step {i}: '{how}' needs an object")
        sided_gesture = bool(gesture) and gesture in ONE_HANDED_GESTURES
        if hand != "any" and not sided_gesture and how == "show":
            raise ValueError(
                f"step {i}: choose a hand only for a one-hand body action "
                "or for holding, pouring or moving an object"
            )

        requires: list[dict[str, Any]] = []
        if gesture:
            g: dict[str, Any] = {"gesture": gesture, "hold_frames": HOLD_FRAMES}
            if hand != "any" and sided_gesture:
                g["side"] = hand
            requires.append(g)
        if obj:
            oid = slug(obj)
            if how == "show":
                # Beside a gesture the object only has to be in view; alone, it
                # is the whole step and must be held.
                d: dict[str, Any] = {"detect": obj, "hold_frames": 3 if gesture else HOLD_FRAMES}
                if scene:
                    d["min_area"] = PRESENT_AREA
                requires.append(d)
            else:
                c: dict[str, Any] = {"contact": ["hand", oid], "hold_frames": 4}
                if hand != "any":
                    c["side"] = hand
                requires.append(c)
                if how == "pour":
                    requires.append({"tilted": oid, "hold_frames": 4})
                elif how == "move":
                    requires.append({"moved": oid, "min_frac": MOVE_FRAC})
            if obj not in classes:
                classes.append(obj)
        name_i = step_name(
            {
                "object": obj,
                "gesture": gesture,
                "hand": hand,
                "how": how,
                "instruction": spec.get("instruction"),
            }
        )
        out_steps.append(
            {
                "id": f"s{i}",
                "name": name_i,
                "voice": f"Step {i}. {name_i}.",
                "preconditions": [f"s{i - 1}"] if i > 1 else [],
                "requires": requires,
                "timeout_s": 120,
                "on_timeout": "stall",
            }
        )

    return {
        "procedure": {
            "id": proc_id,
            "name": name.strip() or "Custom experiment",
            "version": 1,
            "rack_markers": "DICT_4X4_50",
            "vocabulary": vocabulary,
        },
        "markers": [],
        "regions": [],
        "objects": [{"id": slug(c), "classes": [c]} for c in classes],
        "steps": out_steps,
    }


def compose(steps: list[dict[str, Any]], name: str, proc_id: str = "custom") -> Procedure:
    """A validated procedure from builder steps."""
    return Procedure.model_validate(procedure_dict(steps, name, proc_id))


def spec_of(proc: Procedure) -> list[dict[str, Any]]:
    """Builder steps read back from a procedure, for editing it again."""
    out = []
    for step in proc.steps:
        spec: dict[str, Any] = {
            "object": None,
            "gesture": None,
            "instruction": step.name,
            "hand": "any",
            "how": "show",
        }
        for p in step.requires:
            if p.kind == "detect":
                spec["object"] = p.obj_class
            elif p.kind == "gesture":
                spec["gesture"] = p.gesture
                if p.side in ("left", "right"):
                    spec["hand"] = p.side
            elif p.kind == "contact":
                spec["object"] = sorted(proc.classes_for(p.b) or {p.b})[0]
                if spec["how"] == "show":
                    spec["how"] = "hold"
                if p.side in ("left", "right"):
                    spec["hand"] = p.side
            elif p.kind == "tilted":
                spec["how"] = "pour"
            elif p.kind == "moved":
                spec["how"] = "move"
        if step.name == step_name({**spec, "instruction": None}):
            spec["instruction"] = None  # the default wording; keep it default
        out.append(spec)
    return out


class ExperimentStore:
    """Saved experiments: one YAML procedure file each, in their own folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, exp_id: str) -> Path:
        return self.root / f"{exp_id}.yaml"

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.yaml"))

    def save(
        self,
        name: str,
        steps: list[dict[str, Any]],
        *,
        exp_id: str | None = None,
        taken: set[str] | frozenset[str] = frozenset(),
    ) -> str:
        """Write the experiment; return its id.

        A new experiment takes the slug of its name, suffixed if that id is
        already used here or by the built-in library (``taken``). Passing
        ``exp_id`` overwrites that experiment -- the edit path.
        """
        if exp_id is None:
            base = slug(name)
            exp_id, n = base, 2
            while exp_id in taken or self.path(exp_id).exists():
                exp_id, n = f"{base}_{n}", n + 1
        data = procedure_dict(steps, name, exp_id)
        Procedure.model_validate(data)  # never write a file that cannot be loaded
        summary = " -> ".join(s["name"] for s in data["steps"])
        header = (
            f"# {exp_id.upper()} — {data['procedure']['name']}\n"
            "#\n"
            f"# Saved from the dashboard on {time.strftime('%Y-%m-%d %H:%M')}. "
            f"{len(data['steps'])} steps: {summary}\n\n"
        )
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
        self.path(exp_id).write_text(header + body, encoding="utf-8")
        return exp_id

    def delete(self, exp_id: str) -> bool:
        p = self.path(exp_id)
        if not p.is_file():
            return False
        p.unlink()
        return True
