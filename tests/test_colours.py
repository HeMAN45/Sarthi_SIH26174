"""Objects found by their colour, and the cube experiment built on it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orbital_har.core.types import AlertKind, StepState
from orbital_har.perception import colours
from orbital_har.perception.detect import Detector
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import make_engine
from tests.test_hands_and_motion import Rig

BLUE, RED, YELLOW = (200, 60, 20), (30, 30, 210), (20, 210, 230)  # BGR
PROCEDURES = Path(__file__).resolve().parents[1] / "procedures"


def table(*blocks) -> np.ndarray:
    """A grey 640x480 scene with coloured rectangles (x0, y0, x1, y1, bgr)."""
    img = np.full((480, 640, 3), 128, np.uint8)
    for x0, y0, x1, y1, bgr in blocks:
        cv2.rectangle(img, (x0, y0), (x1, y1), bgr, -1)
    return img


def test_a_blue_cube_is_found_where_it_is() -> None:
    (hit,) = colours.find(table((100, 300, 160, 360, BLUE)), {"blue_cube": "blue"})
    cls, conf, (x0, _y0, _x1, y1), _ = hit
    assert cls == "blue_cube" and conf > 0.8
    assert abs(x0 - 100) < 6 and abs(y1 - 360) < 6


def test_a_half_covered_cube_is_still_the_cube() -> None:
    img = table((100, 300, 160, 360, BLUE))
    cv2.rectangle(img, (100, 330), (130, 360), (60, 90, 150), -1)  # a hand over a corner
    assert colours.find(img, {"blue_cube": "blue"})


@pytest.mark.parametrize(
    "block",
    [
        (0, 100, 640, 130, BLUE),  # a long stripe: a sleeve, a table edge
        (0, 0, 400, 400, BLUE),  # most of the view: a shirt, the sky
        (300, 200, 304, 204, BLUE),  # a speck
    ],
)
def test_what_is_not_a_block_is_ignored(block) -> None:
    assert colours.find(table(block), {"blue_cube": "blue"}) == []


def test_each_colour_finds_its_own_block_red_included() -> None:
    img = table((50, 50, 110, 110, RED), (300, 300, 360, 360, YELLOW))
    found = {
        c: box for c, _, box, _ in colours.find(img, {"red_box": "red", "yellow_box": "yellow"})
    }
    assert found["red_box"][0] < 60 and found["yellow_box"][0] > 290
    assert colours.find(img, {"blue_cube": "blue"}) == []


def test_the_detector_reports_colour_classes_beside_its_model() -> None:
    model = SimpleNamespace(
        names={0: "bottle"}, predict=lambda *a, **k: [SimpleNamespace(boxes=[])]
    )
    det = Detector(model)
    det.set_colours({"blue_cube": "blue"})
    assert "blue_cube" in det.all_classes
    result = det.detect(table((100, 300, 160, 360, BLUE)), {"blue_cube"}, multi=True)
    assert [o["cls"] for o in result.objects] == ["blue_cube"]
    det.set_colours({})  # another procedure loaded
    assert "blue_cube" not in det.all_classes


def test_an_unknown_colour_is_refused_at_load() -> None:
    with pytest.raises(ValueError):
        Procedure.model_validate(
            {
                "procedure": {"id": "x", "name": "X"},
                "objects": [{"id": "c", "classes": ["c"], "color": "teal"}],
                "steps": [{"id": "s1", "name": "S", "voice": "S.", "requires": [{"detect": "c"}]}],
            }
        )


# ------------------------------------------------------------------ the experiment

AT_A = (80, 330, 140, 390)  # inside position A (640x480, true camera view)
HELD = (300, 150, 360, 210)  # lifted, between the two
AT_B = (480, 330, 540, 390)  # inside position B


def cube() -> Procedure:
    return Procedure.load(PROCEDURES / "cube_transfer.yaml")


def test_the_cube_procedure_needs_no_training() -> None:
    proc = cube()
    assert proc.colour_classes == {"blue_cube": "blue"}
    assert proc.is_scene


def test_moving_the_cube_with_the_right_hand_completes_every_step() -> None:
    rig = Rig(cube())
    rig.engine = make_engine(cube(), "clean")
    rig.frames(15, boxes={"blue_cube": AT_A})
    assert rig.state("s1") == StepState.COMPLETE
    rig.frames(6, boxes={"blue_cube": AT_A}, touch={"blue_cube": "right"})
    assert rig.state("s2") == StepState.COMPLETE
    rig.frames(5, boxes={"blue_cube": HELD}, touch={"blue_cube": "right"})
    rig.frames(25, boxes={"blue_cube": AT_B})
    assert rig.engine.is_complete


def test_picking_it_up_with_the_left_hand_is_called_out() -> None:
    rig = Rig(cube())
    rig.engine = make_engine(cube(), "clean")
    rig.frames(15, boxes={"blue_cube": AT_A}).frames(30, boxes={"blue_cube": AT_A})
    rig.frames(8, boxes={"blue_cube": AT_A}, touch={"blue_cube": "left"})
    assert [a["step_id"] for a in rig.alerts(AlertKind.WRONG_HAND)] == ["s2"]
    assert rig.state("s2") != StepState.COMPLETE


def test_strict_mode_flags_reaching_b_without_the_right_hand_pick_up() -> None:
    rig = Rig(cube())
    rig.engine = make_engine(cube(), "strict")
    rig.frames(15, boxes={"blue_cube": AT_A})
    rig.frames(25, boxes={"blue_cube": AT_B})  # moved, but never in the right hand
    assert rig.state("s3") == StepState.OUT_OF_ORDER
