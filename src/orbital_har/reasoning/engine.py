"""The procedure engine: step state machine and verdicts.

Consumes perception events, emits ``step_state`` and ``alert`` events. Knows
nothing about cameras, models, voices or screens.

Two behaviours worth reading the code for, because they are judgement calls
rather than mechanics:

*Completion does not require preconditions.* By default a step completes when
its evidence says it happened, whether or not the procedure said it was allowed
to happen yet. The system reports reality; it does not refuse to believe its own
eyes. Precondition violations surface as skip alerts on the steps that were
passed over. Set ``strict_preconditions`` to gate completion instead.

*One event, one alert.* Doing step 5 with step 4 undone is a single mistake. It
raises one skip alert naming step 4, not also an out-of-order alert naming
step 5. Alert noise is the fastest way to get an assistant muted, and a muted
assistant has no value (PRD NFR-04).

*Only nearby steps may complete.* Evidence is evaluated for steps within
``completion_lookahead`` of the frontier, never the whole procedure. Without
this, any step whose predicates happen to describe the world's resting state
fires on frame one: "close both boxes" is trivially true before anyone has
opened them. Ambient state is not proof that the operator did something. The
cost is that jumping more than the lookahead ahead goes undetected, which is
the safer failure -- we stay silent rather than inventing a verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from orbital_har.core.bus import EventBus
from orbital_har.core.types import (
    RESOLVED_STATES,
    SATISFYING_STATES,
    AlertKind,
    Event,
    EventType,
    Severity,
    StepState,
)
from orbital_har.reasoning.predicates import EvalContext, PredicateResult, describe, evaluate
from orbital_har.reasoning.schema import Procedure, StepDef
from orbital_har.reasoning.window import FrameSnapshot, SnapshotAssembler, Window

_CONSUMED = {
    EventType.FRAME.value,
    EventType.DETECTION.value,
    EventType.CONTACT.value,
    EventType.RACK.value,
    EventType.HAND.value,
    EventType.POSE.value,
}


@dataclass
class EngineConfig:
    tau_complete: float = 0.75
    tau_abstain: float = 0.50
    #: How long evidence must sit below tau_abstain before we declare we cannot verify.
    unverified_dwell_s: float = 1.5
    #: Extra step-level confirmation on top of each predicate's hold_frames.
    step_hold_frames: int = 1
    #: When true, a step cannot complete with unmet preconditions.
    strict_preconditions: bool = False
    #: How far past the frontier a step may be and still be judged. Keeps
    #: resting-state evidence in far-future steps from firing spuriously.
    completion_lookahead: int = 2
    window_capacity: int = 240
    #: D-07 free-float advisory. A tether-required object moving faster than
    #: this while nothing is touching it is drifting, not being handled.
    free_float_speed_mm_s: float = 250.0
    free_float_window_s: float = 0.6
    #: One advisory per object per cooldown. A drifting object stays drifting
    #: for many frames, and repeating the same warning is how crews learn to
    #: ignore warnings (PRD NFR-04).
    free_float_cooldown_s: float = 8.0


@dataclass(frozen=True, slots=True)
class StepEvaluation:
    satisfied: bool
    #: Weakest contributing predicate -- what the verdict is judged on.
    confidence: float
    #: Strongest contributing predicate. Only used to date the first sign of
    #: activity, so a step that was never formally active still reports a
    #: sensible duration.
    best_confidence: float
    evidence: list[str]


@dataclass
class StepRuntime:
    step: StepDef
    ordinal: int
    state: StepState = StepState.PENDING
    activated_t: float | None = None
    first_evidence_t: float | None = None
    resolved_t: float | None = None
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reason: str = ""
    streak: int = 0
    low_conf_since: float | None = None
    _latched: dict[str, PredicateResult] = field(default_factory=dict)

    @property
    def origin_t(self) -> float | None:
        return self.activated_t if self.activated_t is not None else self.first_evidence_t

    @property
    def duration_s(self) -> float | None:
        if self.resolved_t is None or self.origin_t is None:
            return None
        return round(self.resolved_t - self.origin_t, 2)


class Engine:
    """Drives step state from the perception event stream."""

    def __init__(
        self,
        procedure: Procedure,
        config: EngineConfig | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.procedure = procedure
        self.config = config or EngineConfig()
        self.bus = bus
        self.window = Window(capacity=self.config.window_capacity)
        self._assembler = SnapshotAssembler()
        self._seq = 0
        self.started_t: float | None = None
        self.runtimes: list[StepRuntime] = [
            StepRuntime(step=s, ordinal=i) for i, s in enumerate(procedure.steps)
        ]
        self._by_id = {rt.step.id: rt for rt in self.runtimes}
        #: object id -> time of its last free-float advisory.
        self._free_float_last: dict[str, float] = {}

    # ------------------------------------------------------------- accessors

    def state_of(self, step_id: str) -> StepState:
        return self._by_id[step_id].state

    @property
    def states(self) -> dict[str, StepState]:
        return {rt.step.id: rt.state for rt in self.runtimes}

    @property
    def active_steps(self) -> list[StepRuntime]:
        return [rt for rt in self.runtimes if rt.state == StepState.ACTIVE]

    @property
    def next_step(self) -> StepRuntime | None:
        for rt in self.runtimes:
            if rt.state not in RESOLVED_STATES:
                return rt
        return None

    @property
    def is_complete(self) -> bool:
        return all(rt.state in RESOLVED_STATES for rt in self.runtimes)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for rt in self.runtimes:
            counts[rt.state.value] = counts.get(rt.state.value, 0) + 1
        return {
            "procedure": self.procedure.procedure.id,
            "steps_total": len(self.runtimes),
            "counts": counts,
            "complete": self.is_complete,
        }

    # ----------------------------------------------------------------- input

    def on_event(self, event: Event) -> list[Event]:
        """Feed one event. Returns any verdict events it produced."""
        if event.type == EventType.CREW_ACTION.value:
            return self._on_crew_action(event)
        if event.type not in _CONSUMED:
            return []
        if self.started_t is None:
            self.started_t = event.t

        finished = self._assembler.feed(event)
        if finished is None:
            return []
        return self._tick(finished)

    def flush(self) -> list[Event]:
        """Close the in-flight frame at end of stream."""
        finished = self._assembler.flush()
        return self._tick(finished) if finished is not None else []

    def override(self, step_id: str, t: float, actor: str = "crew") -> list[Event]:
        rt = self._by_id.get(step_id)
        if rt is None or rt.state in (StepState.COMPLETE, StepState.OVERRIDDEN):
            return []
        rt.state = StepState.OVERRIDDEN
        rt.resolved_t = t
        rt.reason = f"override by {actor}"
        out = [self._emit_state(rt, t)]
        out += self._activate_ready(t)
        out += self._ensure_active(t)
        return out

    def skip(self, step_id: str | None, t: float, reason: str = "crew skipped") -> list[Event]:
        """Mark a step skipped at the crew's request.

        ``step_id`` of None targets whatever the crew is currently being asked
        to do -- the active step, else the frontier. A crew skip is a recorded
        decision, not a detection failure, so it carries its reason and still
        raises the skip alert: the telemetry must show the step was not done.
        """
        if step_id is None:
            rt = next((r for r in self.runtimes if r.state == StepState.ACTIVE), None)
            rt = rt or self.next_step
        else:
            rt = self._by_id.get(step_id)
        if rt is None or rt.state in RESOLVED_STATES:
            return []

        rt.state = StepState.SKIPPED
        rt.resolved_t = t
        rt.reason = reason
        out = [
            self._emit_state(rt, t),
            self._emit_alert(
                AlertKind.SKIP,
                Severity.HIGH,
                t,
                step_id=rt.step.id,
                message=f"Step skipped: {rt.step.name}",
            ),
        ]
        out += self._activate_ready(t)
        out += self._ensure_active(t)
        return out

    def _on_crew_action(self, event: Event) -> list[Event]:
        action = event.payload.get("action")
        step_id = event.payload.get("step_id")
        if action == "override" and step_id:
            return self.override(step_id, event.t, event.payload.get("actor", "crew"))
        if action == "skip":
            return self.skip(step_id, event.t, event.payload.get("reason", "crew skipped"))
        return []

    # ------------------------------------------------------------------ tick

    def _tick(self, snapshot: FrameSnapshot) -> list[Event]:
        t = snapshot.t
        self.window.append(snapshot)
        out: list[Event] = []

        out += self._activate_ready(t)

        frontier = self._frontier()
        completions: list[StepRuntime] = []
        for rt in self.runtimes:
            if rt.state in RESOLVED_STATES or not self._eligible(rt, frontier):
                continue
            result = self._evaluate(rt)
            satisfied, confidence = result.satisfied, result.confidence
            rt.confidence = confidence
            rt.evidence = result.evidence
            if result.best_confidence > 0.0 and rt.first_evidence_t is None:
                rt.first_evidence_t = t

            if not satisfied:
                rt.streak = 0
                rt.low_conf_since = None
                continue

            rt.streak += 1
            if rt.streak < self.config.step_hold_frames:
                continue

            if confidence >= self.config.tau_complete:
                completions.append(rt)
            elif confidence < self.config.tau_abstain:
                out += self._maybe_abstain(rt, t)

        for rt in sorted(completions, key=lambda r: r.ordinal):
            if rt.state in RESOLVED_STATES:
                continue
            out += self._complete(rt, t)

        out += self._check_timeouts(t)
        out += self._check_free_float(t)
        out += self._ensure_active(t)
        return out

    def _check_free_float(self, t: float) -> list[Event]:
        """Advise when a tether-required object is drifting untouched (D-07).

        Advisory, not a verdict: it never changes a step's state. A floating
        vial is a housekeeping problem, and conflating it with procedure
        compliance would let a tidy-up failure mark a correct step wrong.
        """
        tethered = self.procedure.tethered_objects()
        if not tethered:
            return []
        frames = self.window.since(t - self.config.free_float_window_s)
        if len(frames) < 2:
            return []

        latest = frames[-1]
        out: list[Event] = []
        for obj_id in sorted(tethered):
            classes = self.procedure.classes_for(obj_id)

            # Held is not adrift. Either end of a contact counts: the operator
            # may be holding the object or holding a tool that holds it.
            if any(c.b_cls in classes or c.a in classes for c in latest.contacts):
                continue

            track = [
                (f.t, det.centroid_mm)
                for f in frames
                if (det := f.best(classes)) is not None and det.centroid_mm is not None
            ]
            if len(track) < 2:
                continue

            # Median of per-interval speeds, not net displacement over the
            # window. A detector jitter or a track-id switch produces one huge
            # interval among many still ones; net displacement reads that as
            # flight, the median reads it as the artefact it is. Drifting
            # objects move on *every* interval, so they survive the median.
            speeds = [
                math.dist(pa, pb) / (tb - ta) for (ta, pa), (tb, pb) in pairwise(track) if tb > ta
            ]
            if len(speeds) < 4:
                continue
            speeds.sort()
            speed = speeds[len(speeds) // 2]
            if speed < self.config.free_float_speed_mm_s:
                continue

            last = self._free_float_last.get(obj_id)
            if last is not None and t - last < self.config.free_float_cooldown_s:
                continue
            self._free_float_last[obj_id] = t
            out.append(
                self._emit_alert(
                    AlertKind.FREE_FLOAT,
                    Severity.MEDIUM,
                    t,
                    message=(
                        f"{obj_id.replace('_', ' ')} is adrift at {speed:.0f} mm/s - secure it."
                    ),
                )
            )
        return out

    def _frontier(self) -> int:
        """Ordinal of the earliest step still awaiting a verdict."""
        for rt in self.runtimes:
            if rt.state not in RESOLVED_STATES:
                return rt.ordinal
        return len(self.runtimes)

    def _eligible(self, rt: StepRuntime, frontier: int) -> bool:
        if rt.state == StepState.ACTIVE:
            return True
        return rt.ordinal <= frontier + self.config.completion_lookahead

    def _evaluate(self, rt: StepRuntime) -> StepEvaluation:
        ctx = EvalContext(window=self.window, procedure=self.procedure, step_started_t=rt.origin_t)
        evidence: list[str] = []
        confidences: list[float] = []
        best = 0.0

        required_ok = True
        for i, p in enumerate(rt.step.requires):
            result = self._resolve_latch(rt, f"r{i}", p, evaluate(p, ctx))
            evidence.append(describe(p, result))
            confidences.append(result.confidence)
            best = max(best, result.confidence)
            if not result.satisfied:
                required_ok = False

        any_ok = True
        if rt.step.any_of:
            any_ok = False
            best_alt = 0.0
            for i, p in enumerate(rt.step.any_of):
                result = self._resolve_latch(rt, f"a{i}", p, evaluate(p, ctx))
                evidence.append(describe(p, result))
                best = max(best, result.confidence)
                if result.satisfied:
                    any_ok = True
                    best_alt = max(best_alt, result.confidence)
            if any_ok:
                confidences.append(best_alt)

        return StepEvaluation(
            satisfied=required_ok and any_ok,
            confidence=min(confidences) if confidences else 0.0,
            best_confidence=best,
            evidence=evidence,
        )

    @staticmethod
    def _resolve_latch(
        rt: StepRuntime, key: str, predicate: Any, result: PredicateResult
    ) -> PredicateResult:
        """Latched predicates stay satisfied for the rest of the activation.

        Needed for evidence that is transient by nature: the operator did hold
        the red box, even though they have long since put it down.
        """
        if not predicate.latch:
            return result
        if result.satisfied:
            rt._latched[key] = result
            return result
        return rt._latched.get(key, result)

    # ---------------------------------------------------------- transitions

    def _activate_ready(self, t: float) -> list[Event]:
        out: list[Event] = []
        for rt in self.runtimes:
            if rt.state != StepState.PENDING:
                continue
            if all(self.state_of(pre) in SATISFYING_STATES for pre in rt.step.preconditions):
                out.append(self._activate(rt, t))
        return out

    def _activate(self, rt: StepRuntime, t: float) -> Event:
        rt.state = StepState.ACTIVE
        rt.activated_t = t
        rt.streak = 0
        rt.low_conf_since = None
        rt._latched.clear()
        return self._emit_state(rt, t)

    def _ensure_active(self, t: float) -> list[Event]:
        """Guarantee the crew always has a next instruction.

        If every remaining step is blocked -- which happens after a skip, since
        a skipped step does not satisfy a precondition -- promote the earliest
        unresolved step anyway. An advisor with nothing to say is useless.
        """
        if self.is_complete:
            return []
        if any(rt.state == StepState.ACTIVE for rt in self.runtimes):
            return []
        nxt = self.next_step
        if nxt is None or nxt.state in RESOLVED_STATES:
            return []
        if nxt.state in (StepState.UNVERIFIED, StepState.STALLED):
            return []
        return [self._activate(nxt, t)]

    def _complete(self, rt: StepRuntime, t: float) -> list[Event]:
        unmet = [
            pre for pre in rt.step.preconditions if self.state_of(pre) not in SATISFYING_STATES
        ]

        if unmet and self.config.strict_preconditions:
            if rt.state != StepState.OUT_OF_ORDER:
                rt.state = StepState.OUT_OF_ORDER
                rt.reason = f"preconditions unmet: {unmet}"
                return [
                    self._emit_state(rt, t),
                    self._emit_alert(
                        AlertKind.OUT_OF_ORDER,
                        Severity.HIGH,
                        t,
                        step_id=rt.step.id,
                        expected_step_id=unmet[0],
                        message=(
                            f"Out of sequence: {rt.step.name}. "
                            f"Expected: {self._by_id[unmet[0]].step.name}"
                        ),
                    ),
                ]
            return []

        rt.state = StepState.COMPLETE
        rt.resolved_t = t
        if unmet:
            rt.reason = f"completed with unmet preconditions: {unmet}"
        out = [self._emit_state(rt, t)]

        out += self._mark_skipped(rt, t)

        # A genuinely inverted sequence: a precondition that sits *after* this
        # step. Ordinary "did a later step first" is already covered by skips.
        inverted = [p for p in unmet if self._by_id[p].ordinal > rt.ordinal]
        if inverted:
            out.append(
                self._emit_alert(
                    AlertKind.OUT_OF_ORDER,
                    Severity.HIGH,
                    t,
                    step_id=rt.step.id,
                    expected_step_id=inverted[0],
                    message=(
                        f"Out of sequence: {rt.step.name}. "
                        f"Expected: {self._by_id[inverted[0]].step.name}"
                    ),
                )
            )

        out += self._activate_ready(t)
        return out

    def _mark_skipped(self, completed: StepRuntime, t: float) -> list[Event]:
        siblings = self.procedure.group_members(completed.step.id)
        out: list[Event] = []
        for rt in self.runtimes:
            if rt.ordinal >= completed.ordinal or rt.state in RESOLVED_STATES:
                continue
            if rt.step.id in siblings:
                continue
            rt.state = StepState.SKIPPED
            rt.resolved_t = t
            rt.reason = f"passed over when {completed.step.id} completed"
            out.append(self._emit_state(rt, t))
            out.append(
                self._emit_alert(
                    AlertKind.SKIP,
                    Severity.HIGH,
                    t,
                    step_id=rt.step.id,
                    message=f"Step skipped: {rt.step.name}",
                )
            )
        return out

    def _maybe_abstain(self, rt: StepRuntime, t: float) -> list[Event]:
        if rt.low_conf_since is None:
            rt.low_conf_since = t
            return []
        if t - rt.low_conf_since < self.config.unverified_dwell_s:
            return []
        if rt.state == StepState.UNVERIFIED:
            return []
        rt.state = StepState.UNVERIFIED
        rt.reason = f"confidence {rt.confidence:.2f} below {self.config.tau_abstain}"
        return [
            self._emit_state(rt, t),
            self._emit_alert(
                AlertKind.UNVERIFIED,
                Severity.MEDIUM,
                t,
                step_id=rt.step.id,
                message=f"Cannot verify: {rt.step.name}. Please confirm.",
            ),
        ]

    def _check_timeouts(self, t: float) -> list[Event]:
        out: list[Event] = []
        for rt in self.runtimes:
            if rt.state != StepState.ACTIVE or rt.step.timeout_s is None:
                continue
            if rt.activated_t is None or t - rt.activated_t <= rt.step.timeout_s:
                continue
            action = rt.step.on_timeout
            if action == "ignore":
                continue
            if action == "skip":
                rt.state = StepState.SKIPPED
                rt.resolved_t = t
                rt.reason = "timed out"
                out.append(self._emit_state(rt, t))
                out.append(
                    self._emit_alert(
                        AlertKind.SKIP,
                        Severity.HIGH,
                        t,
                        step_id=rt.step.id,
                        message=f"Step timed out and was skipped: {rt.step.name}",
                    )
                )
                out += self._activate_ready(t)
            else:
                rt.state = StepState.STALLED
                rt.reason = "timed out"
                out.append(self._emit_state(rt, t))
                out.append(
                    self._emit_alert(
                        AlertKind.STALL,
                        Severity.MEDIUM,
                        t,
                        step_id=rt.step.id,
                        message=f"No progress on: {rt.step.name}",
                    )
                )
        return out

    # ------------------------------------------------------------- emission

    def _emit(self, type_: str, payload: dict[str, Any], t: float) -> Event:
        if self.bus is not None:
            return self.bus.emit("engine", type_, payload, t=t)
        self._seq += 1
        return Event(t=t, seq=self._seq, src="engine", type=type_, payload=payload)

    def _emit_state(self, rt: StepRuntime, t: float) -> Event:
        return self._emit(
            EventType.STEP_STATE.value,
            {
                "step_id": rt.step.id,
                "ordinal": rt.ordinal,
                "name": rt.step.name,
                "voice": rt.step.voice,
                "state": rt.state.value,
                "confidence": round(rt.confidence, 3),
                "duration_s": rt.duration_s,
                "evidence": list(rt.evidence),
                "reason": rt.reason,
            },
            t,
        )

    def _emit_alert(
        self,
        kind: AlertKind,
        severity: Severity,
        t: float,
        *,
        step_id: str | None = None,
        expected_step_id: str | None = None,
        message: str = "",
    ) -> Event:
        return self._emit(
            EventType.ALERT.value,
            {
                "kind": kind.value,
                "severity": severity.value,
                "step_id": step_id,
                "expected_step_id": expected_step_id,
                "message": message,
            },
            t,
        )
