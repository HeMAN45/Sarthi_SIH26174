"""Live camera demo for ORBITAL-HAR.

Webcam -> pretrained YOLO detection -> the REAL reasoning engine -> on-screen
HUD + offline voice prompts and skip alerts.

The detector is a general pretrained COCO model used as a STAND-IN for the
trained BAS-prop model (the next milestone). Everything downstream -- the event
stream, the procedure engine, next-step prompting, skip / out-of-order
detection, and voice -- is the real system, running live.

Run:
    uv run python scripts/live_demo.py --procedure demo_live

Keys:  q = quit   r = restart the run
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from pathlib import Path

import cv2

from orbital_har.core.types import (
    RESOLVED_STATES,
    AlertKind,
    Event,
    EventType,
    Severity,
    StepState,
)
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure

# ---------------------------------------------------------------------------
# Offline voice (Windows SAPI5 via pyttsx3) -- runs in its own thread so the
# video loop never blocks on speech.
# ---------------------------------------------------------------------------


class Voice:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._q: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        if enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 170)
        except Exception as exc:  # pragma: no cover - demo aid
            print(f"[voice] disabled ({exc})")
            self.enabled = False
            return
        while True:
            text = self._q.get()
            if text == "__STOP__":
                break
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception:
                pass

    def say(self, text: str) -> None:
        if self.enabled and text:
            self._q.put(text)

    def stop(self) -> None:
        if self.enabled:
            self._q.put("__STOP__")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_STATE_COLOR = {
    StepState.PENDING.value: (150, 150, 150),
    StepState.ACTIVE.value: (255, 200, 0),
    StepState.COMPLETE.value: (0, 200, 0),
    StepState.SKIPPED.value: (0, 0, 255),
    StepState.OUT_OF_ORDER.value: (0, 0, 255),
    StepState.UNVERIFIED.value: (0, 200, 255),
    StepState.STALLED.value: (0, 200, 255),
    StepState.OVERRIDDEN.value: (255, 0, 255),
}


_MODE_LABEL = {
    "clean": "clean (in-order)",
    "strict": "strict (out-of-order)",
}


def draw_hud(frame, engine: Engine, alert: tuple[str, float] | None, model_note: str,
             mode: str = "clean"):
    h, w = frame.shape[:2]
    panel_w = 430
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (panel_w, h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    y = 34
    cv2.putText(frame, "ORBITAL-HAR  (live)", (16, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    y += 26
    cv2.putText(frame, model_note, (16, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 180, 180), 1)
    y += 18
    cv2.putText(frame, "Present ONE object, held close to the camera.", (16, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 200, 255), 1)
    y += 18
    cv2.putText(frame, f"mode: {_MODE_LABEL.get(mode, mode)}", (16, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 0), 1)
    y += 30

    for rt in engine.runtimes:
        color = _STATE_COLOR.get(rt.state.value, (200, 200, 200))
        label = f"{rt.step.id}  {rt.step.name}"
        cv2.putText(frame, label, (16, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 1)
        cv2.putText(frame, rt.state.value.upper(), (16, y + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, color, 1)
        if rt.confidence:
            cv2.putText(frame, f"conf {rt.confidence:.2f}", (250, y + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.44, color, 1)
        y += 50

    nxt = engine.next_step
    y += 6
    if nxt is not None:
        cv2.putText(frame, "NEXT:", (16, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
        cv2.putText(frame, nxt.step.voice[:44], (16, y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (255, 255, 0), 1)
    elif engine.is_complete:
        cv2.putText(frame, "PROCEDURE COMPLETE", (16, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 0), 2)

    # Alert banner across the top of the video area.
    if alert is not None and time.time() - alert[1] < 4.0:
        cv2.rectangle(frame, (panel_w, 0), (w, 54), (0, 0, 200), -1)
        cv2.putText(frame, alert[0], (panel_w + 16, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    cv2.putText(frame, "k skip current   r restart   o out-of-order   s summary   q quit",
                (16, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (200, 200, 200), 1)


def draw_summary(frame, engine: Engine, alert_count: int, mode: str) -> None:
    """End-of-run summary card overlaid on the video area."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = int(w * 0.30), int(h * 0.16), int(w * 0.97), int(h * 0.90)
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (15, 15, 15), -1)
    cv2.addWeighted(overlay, 0.82, frame, 0.18, 0, frame)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 0), 2)

    counts: dict[str, int] = {}
    resolved_ts = []
    for rt in engine.runtimes:
        counts[rt.state.value] = counts.get(rt.state.value, 0) + 1
        if rt.resolved_t is not None:
            resolved_ts.append(rt.resolved_t)
    duration = 0.0
    if engine.started_t is not None and resolved_ts:
        duration = max(resolved_ts) - engine.started_t

    # Derive alerts from the SAME state the rows show, so the two can never
    # disagree: every skip / out-of-order / unverified / stall raised an alert.
    alerts_from_state = (
        counts.get("skipped", 0)
        + counts.get("out_of_order", 0)
        + counts.get("unverified", 0)
        + counts.get("stalled", 0)
    )
    alert_count = max(alert_count, alerts_from_state)

    x = x0 + 26
    y = y0 + 46
    title = "RUN COMPLETE" if engine.is_complete else "RUN SUMMARY"
    cv2.putText(frame, title, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 220, 0), 2)
    y += 34
    cv2.putText(frame, f"{engine.procedure.procedure.name}", (x, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    y += 20
    cv2.putText(frame, f"mode: {_MODE_LABEL.get(mode, mode)}", (x, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    y += 40

    rows = [
        ("Steps total", str(len(engine.runtimes)), (255, 255, 255)),
        ("Complete", str(counts.get("complete", 0)), (0, 220, 0)),
        ("Skipped", str(counts.get("skipped", 0)), (0, 0, 255)),
        ("Out of order", str(counts.get("out_of_order", 0)), (0, 0, 255)),
        ("Unverified", str(counts.get("unverified", 0)), (0, 200, 255)),
        ("Alerts raised", str(alert_count), (0, 140, 255)),
        ("Duration", f"{duration:.1f}s", (255, 255, 255)),
    ]
    for label, value, color in rows:
        cv2.putText(frame, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (190, 190, 190), 1)
        cv2.putText(frame, value, (x + 260, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2)
        y += 36

    y += 6
    cv2.putText(frame, "k skip current   r restart   o out-of-order   s hide   q quit", (x, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


#: Demo modes. ``lookahead`` is how far past the current step the engine will
#: accept evidence:
#:   clean  -> 0: only the current step can complete. A misread of a *future*
#:             step's object (e.g. a rotated bottle read as "cell phone") cannot
#:             jump ahead. Rock-solid for the happy-path walkthrough.
#:   skip   -> 1: exactly ONE step ahead may complete, so you can skip a single
#:             step and have it flagged -- but a misread cannot teleport to the
#:             last step. Skipping is demonstrated one step at a time.
#:   strict -> 1 + strict_preconditions: doing the next step early is flagged
#:             out-of-order instead of completing.
_MODE_LOOKAHEAD = {"clean": 0, "strict": 1}


def skip_current_step(engine: Engine, t: float) -> list[Event]:
    """Crew action: skip the current step in place and advance the queue.

    Used when the object for the active step is unavailable. Does NOT restart the
    run -- it marks the active step skipped, raises a skip alert, and activates
    the next step, exactly as a genuine skip would appear.
    """
    rt = next((r for r in engine.runtimes if r.state == StepState.ACTIVE), None)
    if rt is None:
        rt = engine.next_step
    if rt is None or rt.state in RESOLVED_STATES:
        return []
    rt.state = StepState.SKIPPED
    rt.resolved_t = t
    rt.reason = "crew skipped (object unavailable)"
    out = [
        engine._emit_state(rt, t),
        engine._emit_alert(
            AlertKind.SKIP, Severity.HIGH, t,
            step_id=rt.step.id, message=f"Step skipped: {rt.step.name}",
        ),
    ]
    out += engine._activate_ready(t)
    out += engine._ensure_active(t)
    return out


def make_engine(proc: Procedure, mode: str = "clean") -> Engine:
    lookahead = _MODE_LOOKAHEAD.get(mode, 0)
    return Engine(
        proc,
        EngineConfig(
            tau_complete=0.60,
            tau_abstain=0.35,
            window_capacity=120,
            strict_preconditions=(mode == "strict"),
            completion_lookahead=lookahead,
        ),
    )


def run(args: argparse.Namespace) -> int:
    from ultralytics import YOLO

    proc_path = Path(args.procedure)
    if not proc_path.exists():
        proc_path = Path("procedures") / f"{args.procedure}.yaml"
    proc = Procedure.load(proc_path)

    wanted = proc.vocabulary_classes  # {"bottle", "book", "cell phone"}

    print("[demo] loading detector ...")
    model = YOLO(args.model)
    names = model.names  # id -> class name

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"[demo] ERROR: cannot open camera {args.camera}")
        return 1

    if args.preview:
        print("[demo] PREVIEW: hold each object up; note the label it gets. q to quit.")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            res = model.predict(frame, conf=0.35, verbose=False)[0]
            fa = float(frame.shape[0] * frame.shape[1])
            for box in res.boxes:
                cls_name = names[int(box.cls[0])]
                conf = float(box.conf[0])
                x0, y0, x1, y1 = (float(v) for v in box.xyxy[0])
                af = max(0.0, x1 - x0) * max(0.0, y1 - y0) / fa
                col = (0, 220, 0) if cls_name in wanted else (0, 170, 255)
                cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)), col, 2)
                cv2.putText(frame, f"{cls_name} {conf:.2f} {af*100:.0f}%",
                            (int(x0), int(y0) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            cv2.putText(frame, "PREVIEW  green=procedure object  orange=other   q quit",
                        (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(frame, f"procedure classes: {sorted(wanted)}",
                        (16, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1)
            cv2.imshow("ORBITAL-HAR live demo", frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
        cap.release()
        cv2.destroyAllWindows()
        return 0

    voice = Voice(enabled=not args.no_voice)
    mode = "strict" if args.strict else "clean"
    engine = make_engine(proc, mode)
    spoken_active: set[str] = set()
    alert: tuple[str, float] | None = None
    alert_count = 0
    show_summary = False
    summary_spoken = False
    fid = 0
    seq = 0
    model_note = "detector: pretrained COCO stand-in (trained BAS model = next milestone)"

    def handle(evs: list[Event], t: float) -> None:
        """Route engine verdicts to voice, the alert banner and the counter."""
        nonlocal alert, alert_count
        for ev in evs:
            p = ev.payload
            if ev.type == EventType.STEP_STATE.value:
                if p["state"] == StepState.ACTIVE.value and p["step_id"] not in spoken_active:
                    voice.say(p["voice"])
                    spoken_active.add(p["step_id"])
                elif p["state"] == StepState.COMPLETE.value:
                    print(f"[demo] COMPLETE {p['step_id']}  {p['name']}  conf {p['confidence']}")
            elif ev.type == EventType.ALERT.value:
                alert = (p["message"], t)
                alert_count += 1
                voice.say(f"Attention. {p['message']}")
                print(f"[demo] ALERT {p['kind']}: {p['message']}")

    # Speak the first prompt.
    if engine.next_step is not None:
        voice.say(engine.next_step.step.voice)
        spoken_active.add(engine.next_step.step.id)

    print("[demo] running.  r restart | k skip current step | o out-of-order | q quit")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = time.time()
        fid += 1

        res = model.predict(frame, conf=0.35, verbose=False)[0]
        frame_area = float(frame.shape[0] * frame.shape[1])

        # Gather candidate detections of the procedure's objects.
        candidates = []  # (area_frac, cls_name, conf, (x0,y0,x1,y1))
        for box in res.boxes:
            cls_name = names[int(box.cls[0])]
            if cls_name not in wanted:
                continue
            conf = float(box.conf[0])
            x0, y0, x1, y1 = (float(v) for v in box.xyxy[0])
            area_frac = max(0.0, x1 - x0) * max(0.0, y1 - y0) / frame_area
            candidates.append((area_frac, cls_name, conf, (x0, y0, x1, y1)))

        # Only the largest object that is held CLOSE (above min-area) counts as
        # "presented". This ignores clutter lying in the background of the frame
        # and requires the operator to actually show the object to the camera.
        candidates.sort(reverse=True)
        objects = []
        presented = candidates[0] if candidates and candidates[0][0] >= args.min_area else None
        for i, (area_frac, cls_name, conf, (x0, y0, x1, y1)) in enumerate(candidates):
            is_presented = presented is not None and i == 0
            color = (0, 220, 0) if is_presented else (110, 110, 110)
            thick = 3 if is_presented else 1
            cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)), color, thick)
            tag = f"{cls_name} {conf:.2f} {area_frac*100:.0f}%"
            if not is_presented and i == 0:
                tag += "  (hold closer)"
            cv2.putText(frame, tag, (int(x0), int(y0) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        if presented is not None:
            _, cls_name, conf, (x0, y0, x1, y1) = presented
            objects.append({
                "cls": cls_name,
                "conf": round(conf, 3),
                "bbox": [x0, y0, x1, y1],
                "track_id": None,
            })

        # The engine closes the PREVIOUS frame's snapshot when this frame event
        # arrives, so verdicts (alerts, prompts, completions) are returned by the
        # frame call as often as the detection call. Collect both.
        seq += 1
        verdicts = engine.on_event(Event(t=now, seq=seq, src="capture",
                                        type=EventType.FRAME.value,
                                        payload={"frame_id": fid, "w": frame.shape[1],
                                                 "h": frame.shape[0]}))
        seq += 1
        verdicts += engine.on_event(Event(t=now, seq=seq, src="detect",
                                         type=EventType.DETECTION.value,
                                         payload={"frame_id": fid, "objects": objects}))

        handle(verdicts, now)

        # Auto-show the summary card once the run resolves (lenient runs).
        if engine.is_complete and not show_summary:
            show_summary = True
        if show_summary and engine.is_complete and not summary_spoken:
            voice.say("Procedure complete.")
            summary_spoken = True

        draw_hud(frame, engine, alert, model_note, mode)
        if show_summary:
            draw_summary(frame, engine, alert_count, mode)
        cv2.imshow("ORBITAL-HAR live demo", frame)

        def _restart(new_mode: str) -> Engine:
            eng = make_engine(proc, new_mode)
            spoken_active.clear()
            if eng.next_step is not None:
                voice.say(eng.next_step.step.voice)
                spoken_active.add(eng.next_step.step.id)
            print(f"[demo] restarted (mode={new_mode})")
            return eng

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("k"):
            # Skip the current step in place -- no restart, same queue.
            handle(skip_current_step(engine, now), now)
        elif key == ord("s"):
            show_summary = not show_summary
        else:
            new_mode = None
            if key == ord("r"):
                new_mode = "clean"
            elif key == ord("o"):
                new_mode = "strict"
            if new_mode is not None:
                mode = new_mode
                engine = _restart(mode)
                alert = None
                alert_count = 0
                show_summary = False
                summary_spoken = False
                fid = 0

    cap.release()
    cv2.destroyAllWindows()
    voice.stop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ORBITAL-HAR live camera demo")
    ap.add_argument("--procedure", default="demo_live")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--strict", action="store_true",
                    help="start in strict mode (out-of-order enforcement)")
    ap.add_argument("--min-area", type=float, default=0.06,
                    help="min fraction of frame an object must fill to count as 'presented'")
    ap.add_argument("--preview", action="store_true",
                    help="detection-only calibration view (see what each object is labelled)")
    ap.add_argument("--no-voice", action="store_true")
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
