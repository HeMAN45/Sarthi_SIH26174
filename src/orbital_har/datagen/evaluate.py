"""Evaluation: detector metrics, step-level metrics and calibration.

Three questions, and a project is only defensible when it can answer all three:

1. **Does the detector see the props?** mAP per class. Delegated to Ultralytics.
2. **Does the system reach the right verdicts?** Replay recorded sessions through
   the real engine and score the states and alerts it produced. This is the
   number that matters - a detector with excellent mAP that still calls a
   skipped step complete has failed at the actual job.
3. **Are its confidences honest?** A model that says 0.9 should be right about
   nine times in ten. Temperature scaling fixes the scale; ECE measures whether
   it worked; and the τ values the engine abstains on are then *fitted* rather
   than guessed. That is differentiator D-06, and the ``calibrations`` table has
   been sitting empty waiting for it.

**False alerts per ten minutes** is reported separately and deliberately. PRD
NFR-04: false alerts erode crew trust, and a muted assistant has no value. It is
the one metric where a worse score cannot be traded away for a better mean.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orbital_har.core.types import EventType
from orbital_har.reasoning.engine import Engine, EngineConfig, live_config
from orbital_har.reasoning.schema import Procedure

_EPS = 1e-6


# --------------------------------------------------------------------------
# Detector
# --------------------------------------------------------------------------


@dataclass
class DetectorMetrics:
    map50: float = 0.0
    map50_95: float = 0.0
    per_class: dict[str, float] = field(default_factory=dict)
    weights: str = ""

    def report(self) -> str:
        lines = [
            f"detector: {self.weights}",
            f"  mAP@50      {self.map50:.3f}",
            f"  mAP@50-95   {self.map50_95:.3f}",
        ]
        if self.per_class:
            width = max(len(c) for c in self.per_class)
            lines.append("  per class (mAP@50-95):")
            for name, value in sorted(self.per_class.items(), key=lambda kv: kv[1]):
                flag = "  <- weakest" if value == min(self.per_class.values()) else ""
                lines.append(f"    {name:<{width}}  {value:.3f}{flag}")
        return "\n".join(lines)


def evaluate_detector(
    weights: str, data_yaml: Path, imgsz: int = 640, device: str | None = None
) -> DetectorMetrics:
    """Run Ultralytics validation and normalise the numbers we care about."""
    from ultralytics import YOLO

    model = YOLO(weights)
    result = model.val(data=str(data_yaml), imgsz=imgsz, device=device, verbose=False)

    box = result.box
    per_class: dict[str, float] = {}
    names = getattr(result, "names", None) or model.names
    for i, class_index in enumerate(getattr(box, "ap_class_index", []) or []):
        name = names[int(class_index)] if names else str(class_index)
        per_class[name] = float(box.maps[i]) if hasattr(box, "maps") else 0.0

    return DetectorMetrics(
        map50=float(box.map50),
        map50_95=float(box.map),
        per_class=per_class,
        weights=weights,
    )


# --------------------------------------------------------------------------
# Step-level
# --------------------------------------------------------------------------


@dataclass
class StepMetrics:
    """How well the engine's verdicts matched what should have happened."""

    cases: int = 0
    steps_scored: int = 0
    steps_correct: int = 0
    confusion: Counter = field(default_factory=Counter)
    alerts_expected: int = 0
    alerts_matched: int = 0
    alerts_spurious: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.steps_correct / self.steps_scored if self.steps_scored else 0.0

    @property
    def alert_recall(self) -> float:
        return self.alerts_matched / self.alerts_expected if self.alerts_expected else 1.0

    @property
    def false_alerts_per_10min(self) -> float:
        """PRD NFR-04. The metric that cannot be traded away."""
        if self.seconds <= 0:
            return 0.0
        return self.alerts_spurious * 600.0 / self.seconds

    def report(self) -> str:
        lines = [
            f"step verdicts: {self.steps_correct}/{self.steps_scored} correct "
            f"({self.accuracy:.1%}) over {self.cases} case(s), "
            f"{self.seconds / 60:.1f} min of footage",
            f"  alert recall          {self.alert_recall:.1%} "
            f"({self.alerts_matched}/{self.alerts_expected})",
            f"  false alerts / 10 min {self.false_alerts_per_10min:.2f}",
        ]
        wrong = {k: v for k, v in self.confusion.items() if k[0] != k[1]}
        if wrong:
            lines.append("  mistakes (expected -> actual):")
            for (want, got), n in sorted(wrong.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {want:<13} -> {got:<13} x{n}")
        for failure in self.failures:
            lines.append(f"  ! {failure}")
        return "\n".join(lines)


@dataclass
class Case:
    """One scored replay: a procedure, an event stream, and what should happen."""

    name: str
    procedure: Procedure
    events: list[Any]
    expected_states: dict[str, str]
    expected_alerts: list[tuple[str, str | None]] = field(default_factory=list)
    #: The live run mode the case is judged in.
    mode: str = "clean"


def _run(case: Case, config: EngineConfig | None) -> tuple[Engine, list[tuple[str, Any]]]:
    # By default a case is scored as a live run would judge it: numbers for a
    # configuration that never ships would describe nothing.
    engine = Engine(case.procedure, config if config is not None else live_config(case.mode))
    alerts: list[tuple[str, Any]] = []

    def collect(emitted) -> None:
        for event in emitted:
            if event.type == EventType.ALERT.value:
                alerts.append((event.payload["kind"], event.payload.get("step_id")))

    for event in case.events:
        collect(engine.on_event(event))
    collect(engine.flush())
    return engine, alerts


def evaluate_steps(cases: list[Case], config: EngineConfig | None = None) -> StepMetrics:
    """Replay each case and score the verdicts the engine actually reached."""
    metrics = StepMetrics(cases=len(cases))

    for case in cases:
        if not case.events:
            metrics.failures.append(f"{case.name}: no events")
            continue
        engine, alerts = _run(case, config)
        metrics.seconds += case.events[-1].t - case.events[0].t

        for step_id, expected in case.expected_states.items():
            try:
                actual = engine.state_of(step_id).value
            except KeyError:
                metrics.failures.append(f"{case.name}: unknown step {step_id}")
                continue
            metrics.steps_scored += 1
            metrics.confusion[(expected, actual)] += 1
            if actual == expected:
                metrics.steps_correct += 1

        remaining = list(alerts)
        for want in case.expected_alerts:
            metrics.alerts_expected += 1
            if want in remaining:
                remaining.remove(want)
                metrics.alerts_matched += 1
        metrics.alerts_spurious += len(remaining)

    return metrics


def calibration_samples(
    cases: list[Case], config: EngineConfig | None = None
) -> list[tuple[float, bool]]:
    """(confidence, was-it-right) pairs, harvested from replayed verdicts.

    The engine records the confidence it judged each step on. Pairing that with
    whether the verdict matched the expectation gives exactly what temperature
    scaling needs, with no extra labelling.
    """
    samples: list[tuple[float, bool]] = []
    for case in cases:
        if not case.events:
            continue
        engine, _ = _run(case, config)
        for runtime in engine.runtimes:
            expected = case.expected_states.get(runtime.step.id)
            if expected is None or runtime.confidence <= 0.0:
                continue
            samples.append((float(runtime.confidence), runtime.state.value == expected))
    return samples


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def apply_temperature(p: float, temperature: float) -> float:
    """Rescale a confidence. T > 1 softens it, T < 1 sharpens it."""
    return _sigmoid(_logit(p) / max(temperature, _EPS))


def expected_calibration_error(samples: list[tuple[float, bool]], bins: int = 10) -> float:
    """Mean |confidence - accuracy| across equal-width confidence bins."""
    if not samples:
        return 0.0
    totals = [0] * bins
    conf_sum = [0.0] * bins
    correct = [0] * bins
    for p, ok in samples:
        index = min(bins - 1, max(0, int(p * bins)))
        totals[index] += 1
        conf_sum[index] += p
        correct[index] += int(ok)
    n = len(samples)
    error = 0.0
    for i in range(bins):
        if totals[i] == 0:
            continue
        accuracy = correct[i] / totals[i]
        confidence = conf_sum[i] / totals[i]
        error += (totals[i] / n) * abs(confidence - accuracy)
    return error


def fit_temperature(
    samples: list[tuple[float, bool]], lo: float = 0.5, hi: float = 10.0, steps: int = 240
) -> float:
    """Temperature minimising negative log-likelihood.

    A log-spaced scan rather than gradient descent: one parameter, a smooth
    objective, and no optimiser dependency to install on an edge device.

    ``lo`` is 0.5, not near zero, on purpose. On perfectly separable data NLL is
    minimised by driving the temperature towards zero, which collapses every
    confidence to 0 or 1 - a "perfect" likelihood that destroys the ordering the
    thresholds are chosen from. Calibration is meant to soften an overconfident
    model, so the useful range is bounded below.
    """
    if not samples:
        return 1.0
    best_t, best_nll = 1.0, math.inf
    for i in range(steps):
        t = lo * (hi / lo) ** (i / (steps - 1))
        nll = 0.0
        for p, ok in samples:
            q = min(max(apply_temperature(p, t), _EPS), 1.0 - _EPS)
            nll -= math.log(q) if ok else math.log(1.0 - q)
        if nll < best_nll:
            best_t, best_nll = t, nll
    return best_t


@dataclass
class Calibration:
    temperature: float = 1.0
    ece_before: float = 0.0
    ece_after: float = 0.0
    tau_complete: float = 0.75
    tau_abstain: float = 0.50
    samples: int = 0
    dataset_hash: str = ""
    #: True when every sample shares an outcome, so the fit carries no signal.
    degenerate: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.samples > 0 and not self.degenerate

    def report(self) -> str:
        lines = [
            f"calibration on {self.samples} verdict(s)",
            f"  temperature   {self.temperature:.3f}",
            f"  ECE           {self.ece_before:.4f} -> {self.ece_after:.4f}",
            f"  tau_complete  {self.tau_complete:.2f}",
            f"  tau_abstain   {self.tau_abstain:.2f}",
        ]
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        if not self.warnings:
            lines.append(
                "  (keep any predicate min_conf below tau_abstain, or the "
                "abstention path becomes unreachable -- invariant #12)"
            )
        return "\n".join(lines)


def choose_thresholds(
    samples: list[tuple[float, bool]],
    temperature: float = 1.0,
    target_precision: float = 0.95,
    floor: float = 0.05,
) -> tuple[float, float]:
    """Fit τ_complete and τ_abstain from calibrated confidences.

    ``τ_complete`` is the lowest confidence at which the engine is right at
    least ``target_precision`` of the time - act only where the evidence has
    earned it. ``τ_abstain`` is where accuracy falls to a coin toss: below that
    the honest answer is "cannot verify", not a guess.
    """
    if not samples:
        return 0.75, 0.50

    scaled = [(apply_temperature(p, temperature), ok) for p, ok in samples]

    def lowest_threshold_reaching(bar: float) -> float | None:
        """Smallest confidence at which everything above it is ``bar`` precise.

        Evaluated at each *distinct* confidence rather than per sample. A
        threshold admits every tie at once, so scanning sample-by-sample lets a
        few members of a large wrong cohort slip under the precision bar and
        drags the threshold down into that cohort.
        """
        best: float | None = None
        for candidate in sorted({p for p, _ in scaled}, reverse=True):
            above = [ok for p, ok in scaled if p >= candidate]
            if above and sum(above) / len(above) >= bar:
                best = candidate
            else:
                break
        return best

    tau_complete = lowest_threshold_reaching(target_precision) or max(p for p, _ in scaled)
    # Below a coin toss there is nothing to act on: that is the abstain line.
    tau_abstain = lowest_threshold_reaching(0.5) or tau_complete / 2.0

    tau_complete = max(floor + 0.05, min(0.99, tau_complete))
    tau_abstain = max(floor, min(tau_abstain, tau_complete - 0.05))
    return round(tau_complete, 3), round(tau_abstain, 3)


def calibrate(
    samples: list[tuple[float, bool]],
    target_precision: float = 0.95,
    bins: int = 10,
    in_use: tuple[float, float] = (0.75, 0.50),
) -> Calibration:
    """Fit temperature and thresholds, and measure whether it helped.

    Refuses to hand back fitted thresholds when the sample carries no signal.
    A corpus in which every verdict is correct makes "the lowest confidence
    where precision is still 95%" mean "the lowest confidence present" - a
    number that looks like a calibration and is really just the minimum of the
    data. Shipping that as a threshold would quietly lower the bar for acting on
    weak evidence, which is the opposite of what calibration is for.
    """
    # ``in_use`` is (tau_complete, tau_abstain) as the engine runs today. When
    # nothing can be fitted those are what stands, and what gets recorded.
    if not samples:
        return Calibration(
            tau_complete=in_use[0],
            tau_abstain=in_use[1],
            degenerate=True,
            warnings=["no samples - thresholds are the ones in use, not fitted"],
        )

    outcomes = {ok for _, ok in samples}
    temperature = fit_temperature(samples)
    after = [(apply_temperature(p, temperature), ok) for p, ok in samples]
    result = Calibration(
        temperature=round(temperature, 4),
        ece_before=round(expected_calibration_error(samples, bins), 5),
        ece_after=round(expected_calibration_error(after, bins), 5),
        samples=len(samples),
        tau_complete=in_use[0],
        tau_abstain=in_use[1],
    )

    if len(outcomes) < 2:
        result.degenerate = True
        verdict = "correct" if outcomes == {True} else "wrong"
        result.warnings.append(
            f"every verdict was {verdict}, so no threshold can be fitted - "
            "keeping the thresholds in use. Add cases the engine gets WRONG "
            "(occlusion, weak evidence, near-misses) before trusting these."
        )
        return result

    result.tau_complete, result.tau_abstain = choose_thresholds(
        samples, temperature, target_precision=target_precision
    )

    # Invariant #12: a detection floor at or above tau_abstain makes the
    # "cannot verify" path unreachable -- a predicate could never be both
    # satisfied and too weak to trust.
    from orbital_har.reasoning.schema import DETECTION_FLOOR

    if result.tau_abstain <= DETECTION_FLOOR:
        result.warnings.append(
            f"tau_abstain {result.tau_abstain:.2f} is at or below the predicate "
            f"detection floor {DETECTION_FLOOR:.2f}: the abstention path would be "
            "unreachable (invariant #12). Lower DETECTION_FLOOR or gather harder cases."
        )
    return result
