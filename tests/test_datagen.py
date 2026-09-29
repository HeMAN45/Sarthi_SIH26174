"""Dataset building, labelling and evaluation.

No footage and no model required: the corpus is synthesised, the detector is
stubbed, and the step/calibration metrics run against the golden fixtures - the
same ones that gate the reasoning layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orbital_har.datagen import dataset as ds
from orbital_har.datagen import vocabulary as vocab
from orbital_har.datagen.autolabel import to_yolo_line
from orbital_har.datagen.evaluate import (
    Case,
    apply_temperature,
    calibrate,
    calibration_samples,
    choose_thresholds,
    evaluate_steps,
    expected_calibration_error,
    fit_temperature,
)
from orbital_har.datagen.train import TrainConfig
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.store import Store
from orbital_har.simkit.fixtures import ALL_FIXTURES
from orbital_har.simkit.fixtures import build as build_fixture
from tests.conftest import PROCEDURES

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


def test_vocabulary_comes_from_the_procedures() -> None:
    classes = vocab.build(directory=PROCEDURES)
    names = [c.name for c in classes]
    assert "red_box_open" in names
    assert "red_box_closed" in names
    # Demo stand-ins must never reach the trained vocabulary.
    assert "bottle" not in names and "cell phone" not in names


def test_class_indices_are_stable_and_contiguous() -> None:
    """A class index that shifts between runs silently relabels a dataset."""
    first = vocab.build(directory=PROCEDURES)
    second = vocab.build(directory=PROCEDURES)
    assert [c.name for c in first] == [c.name for c in second]
    assert [c.index for c in first] == list(range(len(first)))
    assert [c.name for c in first] == sorted(c.name for c in first)


def test_states_are_their_own_classes() -> None:
    """Invariant #8: object states are detector classes, not a second stage."""
    by_name = {c.name: c for c in vocab.build(directory=PROCEDURES)}
    assert by_name["red_box_open"].is_state
    assert by_name["red_box_closed"].is_state
    assert not by_name["tweezers"].is_state
    assert by_name["red_box_open"].object_id == by_name["red_box_closed"].object_id


def test_tether_requirement_is_carried_through() -> None:
    by_name = {c.name: c for c in vocab.build(directory=PROCEDURES)}
    assert by_name["sample_vial"].tether_required
    assert not by_name["tweezers"].tether_required


def test_prompts_are_written_for_a_text_conditioned_detector() -> None:
    by_name = {c.name: c for c in vocab.build(directory=PROCEDURES)}
    assert by_name["tweezers"].prompt == "a pair of metal tweezers"
    assert "open" in by_name["red_box_open"].prompt


# --------------------------------------------------------------------------
# Dataset layout
# --------------------------------------------------------------------------


def _write_frame(directory: Path, stem: str, boxes: list[tuple[int, float]]) -> None:
    """A 1x1 'image' plus its YOLO label. Nothing here decodes pixels."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (directory / f"{stem}.txt").write_text(
        "\n".join(f"{c} 0.5 0.5 {w:.3f} {w:.3f}" for c, w in boxes) + "\n",
        encoding="utf-8",
    )


def test_scaffold_writes_a_usable_data_yaml(tmp_path: Path) -> None:
    names = vocab.class_names(directory=PROCEDURES)
    path = ds.scaffold(tmp_path, names)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["names"] == dict(enumerate(names))
    assert data["train"] == "images/train"
    for split in ds.SPLITS:
        assert (tmp_path / "images" / split).is_dir()
        assert (tmp_path / "labels" / split).is_dir()


@pytest.mark.parametrize(
    ("stem", "clip"),
    [
        ("take03_0147", "take03"),
        ("take03-0147", "take03"),
        ("run1_0001", "run1"),
        ("single", "single"),
    ],
)
def test_clip_of_groups_frames_by_take(stem: str, clip: str) -> None:
    assert ds.clip_of(stem) == clip


def test_split_keeps_each_clip_whole(tmp_path: Path) -> None:
    """The point of the whole module: no near-duplicate may straddle the split.

    Splitting by frame puts a frame in train and its neighbour in val, and val
    mAP becomes a number about memorisation rather than recognition.
    """
    names = vocab.class_names(directory=PROCEDURES)
    ds.scaffold(tmp_path, names)
    for clip in range(6):
        for frame in range(5):
            _write_frame(tmp_path / "raw", f"take{clip}_{frame:04d}", [(0, 0.2)])

    counts = ds.split_dataset(tmp_path, val_frac=0.34, seed=1)
    assert counts["train"] + counts["val"] == 30

    train_clips = {ds.clip_of(p.stem) for p in (tmp_path / "images" / "train").iterdir()}
    val_clips = {ds.clip_of(p.stem) for p in (tmp_path / "images" / "val").iterdir()}
    assert train_clips and val_clips
    assert not (train_clips & val_clips), "a clip appeared on both sides of the split"


def test_split_moves_labels_with_their_images(tmp_path: Path) -> None:
    ds.scaffold(tmp_path, vocab.class_names(directory=PROCEDURES))
    for clip in range(4):
        _write_frame(tmp_path / "raw", f"take{clip}_0001", [(1, 0.3)])
    ds.split_dataset(tmp_path, val_frac=0.25, seed=0)

    for split in ds.SPLITS:
        for image in (tmp_path / "images" / split).iterdir():
            assert ds.label_path_for(image, tmp_path, split).exists()


def test_split_is_repeatable_without_duplicating(tmp_path: Path) -> None:
    ds.scaffold(tmp_path, vocab.class_names(directory=PROCEDURES))
    for clip in range(4):
        _write_frame(tmp_path / "raw", f"take{clip}_0001", [(0, 0.3)])
    ds.split_dataset(tmp_path, seed=0)

    # A second run has an empty inbox and must not resurrect stale files.
    again = ds.split_dataset(tmp_path, seed=0)
    assert again == {"train": 0, "val": 0}


def test_stats_counts_instances_per_class(tmp_path: Path) -> None:
    names = vocab.class_names(directory=PROCEDURES)
    ds.scaffold(tmp_path, names)
    _write_frame(tmp_path / "images" / "train", "a_0001", [])
    (tmp_path / "labels" / "train" / "a_0001.txt").write_text(
        "0 0.5 0.5 0.2 0.2\n0 0.4 0.4 0.1 0.1\n2 0.6 0.6 0.1 0.1\n", encoding="utf-8"
    )
    _write_frame(tmp_path / "images" / "val", "b_0001", [])
    (tmp_path / "labels" / "val" / "b_0001.txt").write_text("2 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    stats = ds.stats(tmp_path, names)
    assert stats.instances[names[0]] == 2
    assert stats.instances[names[2]] == 2
    assert stats.images == {"train": 1, "val": 1}


def test_stats_reports_an_empty_corpus_as_not_ready(tmp_path: Path) -> None:
    names = vocab.class_names(directory=PROCEDURES)
    ds.scaffold(tmp_path, names)
    stats = ds.stats(tmp_path, names)
    assert not stats.ready
    assert any("no images" in i for i in stats.issues)


def test_stats_flags_a_clip_in_both_splits(tmp_path: Path) -> None:
    names = vocab.class_names(directory=PROCEDURES)
    ds.scaffold(tmp_path, names)
    _write_frame(tmp_path / "images" / "train", "take1_0001", [(0, 0.2)])
    (tmp_path / "labels" / "train" / "take1_0001.txt").write_text(
        "0 .5 .5 .2 .2\n", encoding="utf-8"
    )
    _write_frame(tmp_path / "images" / "val", "take1_0002", [(0, 0.2)])
    (tmp_path / "labels" / "val" / "take1_0002.txt").write_text("0 .5 .5 .2 .2\n", encoding="utf-8")

    stats = ds.stats(tmp_path, names)
    assert not stats.ready
    assert any("BOTH train and val" in i for i in stats.issues)


def test_stats_flags_an_out_of_range_class_index(tmp_path: Path) -> None:
    names = vocab.class_names(directory=PROCEDURES)
    ds.scaffold(tmp_path, names)
    _write_frame(tmp_path / "images" / "train", "a_0001", [])
    (tmp_path / "labels" / "train" / "a_0001.txt").write_text(
        f"{len(names) + 3} .5 .5 .2 .2\n", encoding="utf-8"
    )
    assert any("out of range" in i for i in ds.stats(tmp_path, names).issues)


# --------------------------------------------------------------------------
# Label geometry
# --------------------------------------------------------------------------


def test_yolo_line_normalises_a_box() -> None:
    line = to_yolo_line(3, (100.0, 50.0, 300.0, 250.0), w=400, h=400)
    index, cx, cy, bw, bh = line.split()
    assert int(index) == 3
    assert float(cx) == pytest.approx(0.5)
    assert float(cy) == pytest.approx(0.375)
    assert float(bw) == pytest.approx(0.5)
    assert float(bh) == pytest.approx(0.5)


def test_yolo_line_clamps_a_box_that_leaves_the_frame() -> None:
    """An out-of-frame coordinate silently poisons training if it survives."""
    line = to_yolo_line(0, (-50.0, -20.0, 500.0, 900.0), w=400, h=400)
    _, cx, cy, bw, bh = (float(v) for v in line.split())
    assert 0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0
    assert bw <= 1.0 and bh <= 1.0


# --------------------------------------------------------------------------
# Training config
# --------------------------------------------------------------------------


def test_training_augments_for_a_world_with_no_floor() -> None:
    """Full rotation and vertical flips: the cheap 80% of the HMR clause."""
    kwargs = TrainConfig(data=Path("d.yaml")).to_kwargs()
    assert kwargs["degrees"] == 180.0
    assert kwargs["flipud"] == 0.5


def test_training_defaults_to_the_small_tier_not_nano() -> None:
    assert TrainConfig(data=Path("d.yaml")).weights == "yolo11s.pt"


def test_training_keeps_hue_jitter_tight() -> None:
    """Props are told apart by colour; a wide hue shift merges red and yellow."""
    assert TrainConfig(data=Path("d.yaml")).to_kwargs()["hsv_h"] <= 0.02


# --------------------------------------------------------------------------
# Calibration maths
# --------------------------------------------------------------------------


def test_temperature_of_one_changes_nothing() -> None:
    assert apply_temperature(0.8, 1.0) == pytest.approx(0.8, abs=1e-6)


def test_higher_temperature_softens_confidence() -> None:
    assert apply_temperature(0.95, 3.0) < 0.95
    assert apply_temperature(0.05, 3.0) > 0.05


def test_ece_is_zero_for_a_perfectly_calibrated_sample() -> None:
    samples = [(0.9, True)] * 9 + [(0.9, False)]
    assert expected_calibration_error(samples, bins=10) == pytest.approx(0.0, abs=0.02)


def test_ece_is_large_for_an_overconfident_sample() -> None:
    samples = [(0.99, True)] * 5 + [(0.99, False)] * 5
    assert expected_calibration_error(samples, bins=10) > 0.4


def test_fitting_softens_an_overconfident_model() -> None:
    """Right half the time while claiming 0.99 -> the fit must pull it down."""
    samples = [(0.99, i % 2 == 0) for i in range(200)]
    temperature = fit_temperature(samples)
    assert temperature > 1.0
    assert apply_temperature(0.99, temperature) < 0.9


def test_fitting_reduces_calibration_error() -> None:
    samples = [(0.95, i % 4 != 0) for i in range(200)]
    result = calibrate(samples)
    assert result.ece_after <= result.ece_before


def test_thresholds_are_ordered() -> None:
    samples = [(0.9, True)] * 50 + [(0.3, False)] * 50
    tau_complete, tau_abstain = choose_thresholds(samples)
    assert tau_abstain < tau_complete


def test_a_corpus_with_no_mistakes_is_refused_as_degenerate() -> None:
    """The lowest confidence present is not a calibration."""
    result = calibrate([(0.4, True), (0.6, True), (0.9, True)])
    assert result.degenerate
    assert not result.usable
    assert result.tau_complete == 0.75 and result.tau_abstain == 0.50
    assert any("every verdict was correct" in w for w in result.warnings)


def test_no_samples_is_also_degenerate() -> None:
    result = calibrate([])
    assert result.degenerate and result.samples == 0


def test_a_mixed_corpus_produces_a_real_fit() -> None:
    samples = [(0.95, True)] * 60 + [(0.45, False)] * 40
    result = calibrate(samples)
    assert result.usable
    assert 0.0 < result.tau_abstain < result.tau_complete < 1.0


# --------------------------------------------------------------------------
# Step metrics against the golden corpus
# --------------------------------------------------------------------------


def _golden_cases() -> list[Case]:
    cases = []
    for name in sorted(ALL_FIXTURES):
        fixture = build_fixture(name)
        cases.append(
            Case(
                name=name,
                procedure=Procedure.load(PROCEDURES / f"{fixture.procedure}.yaml"),
                events=fixture.scenario.events,
                expected_states=fixture.expected.final_states,
                expected_alerts=list(fixture.expected.alerts),
                mode=fixture.mode,
            )
        )
    return cases


def test_the_golden_corpus_scores_perfectly() -> None:
    """If this ever drops, the regression is in the engine, not the metric."""
    metrics = evaluate_steps(_golden_cases())
    assert metrics.cases == len(ALL_FIXTURES)
    assert metrics.steps_scored > 0
    assert metrics.accuracy == 1.0
    assert metrics.alert_recall == 1.0
    assert metrics.false_alerts_per_10min == 0.0


def test_false_alert_rate_is_normalised_by_footage_length() -> None:
    """PRD NFR-04: the rate is per unit time, not per run."""
    cases = _golden_cases()
    for case in cases:
        case.expected_alerts = []  # now every raised alert is spurious
    metrics = evaluate_steps(cases)
    assert metrics.alerts_spurious > 0
    assert metrics.false_alerts_per_10min > 0
    assert metrics.seconds > 0


def test_a_wrong_expectation_shows_up_in_the_confusion_table() -> None:
    cases = _golden_cases()
    cases[0].expected_states = dict.fromkeys(cases[0].expected_states, "stalled")
    metrics = evaluate_steps(cases)
    assert metrics.accuracy < 1.0
    assert any(want != got for want, got in metrics.confusion)


def test_calibration_samples_come_out_of_the_replay() -> None:
    samples = calibration_samples(_golden_cases())
    assert samples
    assert all(0.0 < p <= 1.0 for p, _ in samples)


# --------------------------------------------------------------------------
# Model registry and calibration persistence
# --------------------------------------------------------------------------


def test_model_and_calibration_round_trip(tmp_path: Path) -> None:
    """The calibrations table has been in the schema, empty, since day one."""
    store = Store(tmp_path / "s.db")
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"not really weights")

    model_id = store.upsert_model(
        name="bas",
        task="detect",
        version="v1",
        file_path=str(weights),
        classes=vocab.class_names(directory=PROCEDURES),
        metrics={"map50_95": 0.61},
    )
    row = store.get_model(model_id)
    assert row is not None
    assert row["file_sha256"], "weights must be hashed so a run can prove what it used"

    store.insert_calibration(
        model_id=model_id,
        temperature=1.8,
        tau_complete=0.8,
        tau_abstain=0.45,
        dataset_hash="abc123",
        ece=0.03,
    )
    latest = store.latest_calibration(model_id)
    assert latest is not None
    assert latest["temperature"] == pytest.approx(1.8)
    assert latest["tau_abstain"] == pytest.approx(0.45)
    store.close()


def test_calibration_history_is_append_only(tmp_path: Path) -> None:
    """A refit is a new row: the tau a past session ran under must survive."""
    store = Store(tmp_path / "s.db")
    weights = tmp_path / "w.pt"
    weights.write_bytes(b"x")
    model_id = store.upsert_model(name="bas", task="detect", version="v1", file_path=str(weights))
    for temperature in (1.0, 2.0, 3.0):
        store.insert_calibration(
            model_id=model_id,
            temperature=temperature,
            tau_complete=0.8,
            tau_abstain=0.4,
            dataset_hash="d",
        )
    assert store.latest_calibration(model_id)["temperature"] == pytest.approx(3.0)
    store.close()
