"""Experiments built in the dashboard, saved by name and used again.

A saved experiment is an ordinary procedure file. These tests hold it to that:
it loads with the same validator as the built-in library, it reads back into
the builder exactly as it was written, and it survives a new store instance --
which is all "after a restart" means for a file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.experiments import ExperimentStore, compose, spec_of
from orbital_har.runtime.session import perception_needs

DRINK = [
    {"object": "bottle", "instruction": "Pick up the bottle"},
    {"gesture": "hands_together", "object": "bottle", "instruction": "Open the cap"},
    {"gesture": "hand_to_face", "object": "bottle", "instruction": "Take a drink"},
    {"gesture": "hands_together", "object": "bottle"},
    {"object": "bottle"},
]


def test_steps_become_a_valid_procedure() -> None:
    proc = compose(DRINK, "Drink water")
    assert [s.name for s in proc.steps] == [
        "Pick up the bottle",
        "Open the cap",
        "Take a drink",
        "Bring your hands together with the bottle",
        "Present the bottle",
    ]
    assert proc.steps[2].preconditions == ["s2"]
    kinds = [[p.kind for p in s.requires] for s in proc.steps]
    assert kinds[2] == ["gesture", "detect"]
    # A body action in any step turns body tracking on for the run.
    assert perception_needs(proc) == (False, True)


@pytest.mark.parametrize(
    "steps, message",
    [
        ([], "at least one step"),
        ([{"instruction": "Do something"}], "step 1 needs an object"),
        ([{"gesture": "moonwalk"}], "unknown body action"),
        ([{"object": "cup"}] * 16, "15 steps or fewer"),
    ],
)
def test_unusable_steps_are_refused_with_the_reason(steps, message) -> None:
    with pytest.raises(ValueError, match=message):
        compose(steps, "Broken")


def test_a_saved_experiment_is_used_again_after_a_restart(tmp_path: Path) -> None:
    exp_id = ExperimentStore(tmp_path).save("Drink water", DRINK)
    assert exp_id == "drink_water"

    again = ExperimentStore(tmp_path)  # a new process, as far as a file can tell
    assert again.ids() == ["drink_water"]
    proc = Procedure.load(again.path(exp_id))
    assert proc.procedure.name == "Drink water" and proc.procedure.id == "drink_water"
    assert len(proc.steps) == 5


def test_editing_reads_back_exactly_what_was_built(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    exp_id = store.save("Drink water", DRINK)
    spec = spec_of(Procedure.load(store.path(exp_id)))
    assert spec[1] == {
        "object": "bottle",
        "gesture": "hands_together",
        "instruction": "Open the cap",
        "hand": "any",
        "how": "show",
    }
    # Default wording stays default, so renaming an object later renames the step.
    assert spec[3]["instruction"] is None and spec[4]["instruction"] is None

    store.save("Drink water", [*DRINK[:2]], exp_id=exp_id)  # the edit path overwrites
    assert store.ids() == [exp_id]
    assert len(Procedure.load(store.path(exp_id)).steps) == 2


def test_names_never_collide_with_each_other_or_the_library(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    assert store.save("Demo live", DRINK, taken={"demo_live"}) == "demo_live_2"
    assert store.save("Demo live", DRINK, taken={"demo_live"}) == "demo_live_3"


def test_the_file_explains_itself_to_the_library(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    text = store.path(store.save("Drink water", DRINK)).read_text(encoding="utf-8")
    assert text.startswith("# DRINK_WATER - Drink water\n")
    assert "Pick up the bottle -> Open the cap -> Take a drink" in text


def test_delete(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    exp_id = store.save("Temp", [{"object": "cup"}])
    assert store.delete(exp_id) and store.ids() == []
    assert not store.delete(exp_id)


# ------------------------------------------------ hands and what is done with an object


def test_a_step_can_ask_for_one_hand() -> None:
    proc = compose([{"gesture": "hand_raised", "hand": "left"}], "Left hand")
    (p,) = proc.steps[0].requires
    assert (p.gesture, p.side) == ("hand_raised", "left")
    assert proc.steps[0].name == "Raise your left hand"


def test_holding_pouring_and_moving_are_checked_on_the_object_in_hand() -> None:
    proc = compose(
        [
            {"object": "bottle", "how": "hold", "hand": "right"},
            {"object": "bottle", "how": "pour"},
            {"object": "cup", "how": "move", "hand": "left"},
            {"object": "cell phone"},
        ],
        "Pour and carry",
    )
    kinds = [[p.kind for p in s.requires] for s in proc.steps]
    assert kinds == [["contact"], ["contact", "tilted"], ["contact", "moved"], ["detect"]]
    assert proc.steps[0].requires[0].side == "right"
    assert proc.steps[2].requires[1].min_frac is not None  # measured in the picture
    # Holding makes it a scene, so "show the phone" keeps its held-up-close floor.
    assert proc.is_scene and proc.steps[3].requires[0].min_area > 0
    assert [s.name for s in proc.steps] == [
        "Pick up the bottle with your right hand",
        "Pour from the bottle",
        "Move the cup with your left hand",
        "Present the cell phone",
    ]


def test_a_motion_with_an_object_reads_as_the_objects_verb() -> None:
    proc = compose(
        [{"object": "bottle", "gesture": "lifting", "how": "hold", "hand": "right"}], "Lift"
    )
    assert proc.steps[0].name == "Lift the bottle with your right hand"
    assert {p.kind for p in proc.steps[0].requires} == {"gesture", "contact"}


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"gesture": "hands_together", "hand": "left"}], "choose a hand only"),
        ([{"object": "cup", "hand": "left"}], "choose a hand only"),
        ([{"gesture": "hand_raised", "how": "pour"}], "'pour' needs an object"),
        ([{"object": "cup", "how": "juggle"}], "unknown way"),
        ([{"object": "cup", "how": "hold", "hand": "middle"}], "hand must be"),
    ],
)
def test_hands_and_ways_that_make_no_sense_are_refused(steps, message) -> None:
    with pytest.raises(ValueError, match=message):
        compose(steps, "Broken")


def test_hands_and_ways_read_back_for_editing(tmp_path: Path) -> None:
    steps = [
        {"object": "bottle", "how": "pour", "hand": "right"},
        {"gesture": "waving", "hand": "left"},
        {"object": "cup", "how": "move"},
    ]
    store = ExperimentStore(tmp_path)
    back = spec_of(Procedure.load(store.path(store.save("Round trip", steps))))
    assert [(s["object"], s["gesture"], s["hand"], s["how"]) for s in back] == [
        ("bottle", None, "right", "pour"),
        (None, "waving", "left", "show"),
        ("cup", None, "any", "move"),
    ]
    assert all(s["instruction"] is None for s in back)
