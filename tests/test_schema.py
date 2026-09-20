"""Procedure schema validation.

FR-11: the system must refuse to start on an invalid procedure and name the
offending field. Every rejection case here is a failure we would otherwise hit
mid-run in front of a jury.

Cases build a document and mutate it rather than splicing YAML text -- text
surgery silently deletes neighbouring keys and then asserts the wrong error.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml

from orbital_har.reasoning.schema import (
    ContactPredicate,
    DetectPredicate,
    MovedPredicate,
    NearPredicate,
    Procedure,
    ProcedureError,
    check_vocabulary,
)


def base_doc() -> dict[str, Any]:
    return {
        "procedure": {"id": "t", "name": "Test"},
        "markers": ["R"],
        "objects": [{"id": "box", "classes": ["box_open", "box_closed"]}],
        "steps": [
            {
                "id": "s1",
                "name": "One",
                "voice": "one",
                "requires": [{"detect": "box_open"}],
            },
            {
                "id": "s2",
                "name": "Two",
                "voice": "two",
                "preconditions": ["s1"],
                "requires": [{"detect": "box_closed"}],
            },
        ],
    }


def load_doc(doc: dict[str, Any], tmp_path) -> Procedure:
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Procedure.load(path)


def with_step2(tmp_path, **overrides: Any) -> Procedure:
    doc = base_doc()
    doc["steps"][1].update(overrides)
    return load_doc(doc, tmp_path)


# ------------------------------------------------------------- happy paths


def test_shipped_procedures_load(proc_a: Procedure, proc_b: Procedure) -> None:
    assert proc_a.step_ids == [f"s{i}" for i in range(1, 7)]
    assert proc_b.step_ids == [f"s{i}" for i in range(1, 11)]


def test_both_procedures_share_a_vocabulary(proc_a: Procedure, proc_b: Procedure) -> None:
    """D-02 is only honest if loading PROC-B needs no new detector classes."""
    assert proc_b.vocabulary_classes == proc_a.vocabulary_classes


def test_compact_predicate_forms_normalize(tmp_path) -> None:
    proc = with_step2(
        tmp_path,
        requires=[
            {"detect": "box_open", "min_conf": 0.7},
            {"contact": ["hand", "box"], "latch": True},
            {"near": "box", "to": "R", "max_mm": 50},
            {"moved": "box", "min_disp_mm": 30},
        ],
    )
    detect, contact, near, moved = proc.steps[1].requires
    assert isinstance(detect, DetectPredicate)
    assert (detect.obj_class, detect.min_conf) == ("box_open", 0.7)
    assert isinstance(contact, ContactPredicate)
    assert (contact.a, contact.b, contact.latch) == ("hand", "box", True)
    assert isinstance(near, NearPredicate)
    assert (near.obj, near.to, near.max_mm) == ("box", "R", 50)
    assert isinstance(moved, MovedPredicate)
    assert moved.min_disp_mm == 30


def test_predicate_defaults_leave_room_for_abstention(tmp_path) -> None:
    """min_conf is a detection floor. Above tau_abstain it would make the
    'cannot verify' path unreachable -- see DETECTION_FLOOR in schema.py."""
    proc = with_step2(tmp_path, requires=[{"detect": "box_open"}])
    assert proc.steps[1].requires[0].min_conf < 0.50


def test_group_and_ordinal_accessors(proc_b: Procedure) -> None:
    assert proc_b.group_members("s4") == {"s4", "s5"}
    assert proc_b.group_members("s1") == set()
    assert proc_b.ordinal("s3") == 2


def test_object_class_mapping(proc_a: Procedure) -> None:
    assert proc_a.object_for_class("red_box_open") == "red_box"
    assert proc_a.object_for_class("nope") is None
    assert proc_a.classes_for("red_box") == {"red_box_open", "red_box_closed"}


def test_vocabulary_check_reports_missing_classes(proc_a: Procedure) -> None:
    assert check_vocabulary(proc_a, proc_a.vocabulary_classes - {"tweezers"}) == {"tweezers"}
    assert check_vocabulary(proc_a, proc_a.vocabulary_classes) == set()


def test_near_may_target_a_declared_object(tmp_path) -> None:
    doc = base_doc()
    doc["objects"].append({"id": "tray", "classes": ["tray"]})
    doc["steps"][1]["requires"] = [{"near": "box", "to": "tray", "max_mm": 40}]
    assert load_doc(doc, tmp_path).steps[1].requires[0].to == "tray"


# ------------------------------------------------------------- rejections


@pytest.mark.parametrize(
    ("requires", "needle"),
    [
        ([{"detect": "ghost_class"}], "not declared by any object"),
        ([{"near": "box", "to": "ZZ", "max_mm": 5}], "declared marker"),
        ([{"near": "ghost", "to": "R", "max_mm": 5}], "not a declared object"),
        ([{"contact": ["hand", "ghost"]}], "not a declared object"),
        ([{"moved": "ghost", "min_disp_mm": 10}], "not a declared object"),
        ([{"dwell": "box", "region": "nope", "seconds": 1}], "region 'nope' is not declared"),
        ([{"detect": "box_open", "absent": "box_closed"}], "exactly one of"),
        ([{"contact": ["hand"]}], "exactly two participants"),
        ([], "at least one predicate"),
    ],
)
def test_invalid_predicate_is_rejected(tmp_path, requires, needle: str) -> None:
    with pytest.raises(ProcedureError, match=needle):
        with_step2(tmp_path, requires=requires)


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"preconditions": ["nope"]}, "not a known step"),
        ({"preconditions": ["s2"]}, "cannot depend on itself"),
        ({"group": "gx"}, "is not declared"),
        ({"colour": "blue"}, "Extra inputs|extra_forbidden|not permitted"),
    ],
)
def test_invalid_step_attribute_is_rejected(tmp_path, overrides, needle: str) -> None:
    with pytest.raises(ProcedureError, match=needle):
        with_step2(tmp_path, **overrides)


def test_precondition_cycle_is_rejected(tmp_path) -> None:
    doc = base_doc()
    doc["steps"][0]["preconditions"] = ["s2"]
    with pytest.raises(ProcedureError, match="cycle"):
        load_doc(doc, tmp_path)


def test_duplicate_step_ids_are_rejected(tmp_path) -> None:
    doc = base_doc()
    doc["steps"][1]["id"] = "s1"
    with pytest.raises(ProcedureError, match="duplicate step id"):
        load_doc(doc, tmp_path)


def test_duplicate_object_ids_are_rejected(tmp_path) -> None:
    doc = base_doc()
    doc["objects"].append(deepcopy(doc["objects"][0]))
    with pytest.raises(ProcedureError, match="duplicate object id"):
        load_doc(doc, tmp_path)


def test_group_with_unknown_member_is_rejected(tmp_path) -> None:
    doc = base_doc()
    doc["groups"] = [{"id": "g1", "members": ["s1", "ghost"]}]
    with pytest.raises(ProcedureError, match="is not a known step"):
        load_doc(doc, tmp_path)


def test_bad_region_rect_is_rejected(tmp_path) -> None:
    doc = base_doc()
    doc["regions"] = [{"id": "r", "rect": [0.8, 0.1, 0.2, 0.9]}]
    with pytest.raises(ProcedureError, match="rect must be"):
        load_doc(doc, tmp_path)


def test_procedure_with_no_steps_is_rejected(tmp_path) -> None:
    doc = base_doc()
    doc["steps"] = []
    with pytest.raises(ProcedureError, match="no steps"):
        load_doc(doc, tmp_path)


def test_missing_file_is_rejected(tmp_path) -> None:
    with pytest.raises(ProcedureError, match="not found"):
        Procedure.load(tmp_path / "absent.yaml")


def test_malformed_yaml_is_rejected(tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("procedure: {id: t\n  broken", encoding="utf-8")
    with pytest.raises(ProcedureError, match="invalid YAML"):
        Procedure.load(path)


def test_non_mapping_document_is_rejected(tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ProcedureError, match="mapping at the top level"):
        Procedure.load(path)
