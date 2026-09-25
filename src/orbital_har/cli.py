"""Command line entry point.

M0 scope: validate procedures, generate fixture streams, and replay a stream
through the engine printing verdicts. No camera, no models, no dashboard --
that is the point. If this works, the spine of the system is sound.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from orbital_har.core.bus import read_stream
from orbital_har.core.types import EventType, StepState
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure, ProcedureError

PROCEDURE_DIR = Path("procedures")

_STATE_STYLE = {
    StepState.PENDING.value: "dim",
    StepState.ACTIVE.value: "cyan",
    StepState.COMPLETE.value: "green",
    StepState.SKIPPED.value: "red",
    StepState.OUT_OF_ORDER.value: "red",
    StepState.UNVERIFIED.value: "yellow",
    StepState.STALLED.value: "yellow",
    StepState.OVERRIDDEN.value: "magenta",
}

_SEVERITY_STYLE = {"low": "dim", "medium": "yellow", "high": "bold red"}


def resolve_procedure(ref: str) -> Path:
    """Accept a path or a shorthand like 'proc_a'."""
    p = Path(ref)
    if p.exists():
        return p
    candidate = PROCEDURE_DIR / f"{ref}.yaml"
    if candidate.exists():
        return candidate
    raise ProcedureError(f"cannot resolve procedure '{ref}': tried {p} and {candidate}")


def load_procedure(ref: str, console: Console) -> Procedure:
    try:
        return Procedure.load(resolve_procedure(ref))
    except ProcedureError as exc:
        console.print(f"[bold red]Procedure error[/]\n{exc}")
        raise SystemExit(2) from exc


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace, console: Console) -> int:
    proc = load_procedure(args.procedure, console)
    meta = proc.procedure

    console.print(f"[green]OK[/] {meta.name} [dim]({meta.id} v{meta.version})[/]")

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("step")
    table.add_column("name")
    table.add_column("group", style="dim")
    table.add_column("requires", style="dim")
    for i, step in enumerate(proc.steps, start=1):
        table.add_row(
            str(i),
            step.id,
            step.name,
            step.group or "",
            str(len(step.requires) + len(step.any_of)),
        )
    console.print(table)
    console.print(
        f"[dim]{len(proc.objects)} objects, "
        f"{len(proc.vocabulary_classes)} detector classes, "
        f"{len(proc.markers)} markers, {len(proc.regions)} regions[/]"
    )
    return 0


def cmd_fixture(args: argparse.Namespace, console: Console) -> int:
    from orbital_har.simkit.fixtures import ALL_FIXTURES, build

    if args.name == "list":
        for name, factory in sorted(ALL_FIXTURES.items()):
            console.print(f"  [cyan]{name}[/]  [dim]{factory().description}[/]")
        return 0

    fixture = build(args.name)
    out = Path(args.out) if args.out else Path("data/fixtures") / f"{args.name}.jsonl"
    fixture.scenario.write(out)
    console.print(
        f"[green]wrote[/] {out} "
        f"[dim]({len(fixture.scenario.events)} events, "
        f"procedure {fixture.procedure})[/]"
    )
    return 0


def cmd_replay(args: argparse.Namespace, console: Console) -> int:
    proc = load_procedure(args.procedure, console)
    engine = Engine(
        proc,
        EngineConfig(
            tau_complete=args.tau_complete,
            tau_abstain=args.tau_abstain,
            strict_preconditions=args.strict,
        ),
    )

    console.rule(f"[bold]{proc.procedure.name}[/]")
    alerts = 0
    for event in read_stream(args.stream):
        for out in engine.on_event(event):
            alerts += _render(out, console, engine)
    for out in engine.flush():
        alerts += _render(out, console, engine)

    console.rule("[bold]Summary[/]")
    _render_summary(engine, console)
    if args.expect_clean and alerts:
        console.print(f"[bold red]{alerts} alert(s) raised[/]")
        return 1
    return 0


def cmd_demo(args: argparse.Namespace, console: Console) -> int:
    """Build a fixture and replay it in one command -- the M0 milestone."""
    from orbital_har.simkit.fixtures import build

    fixture = build(args.name)
    out = Path("data/fixtures") / f"{args.name}.jsonl"
    fixture.scenario.write(out)
    console.print(f"[dim]{fixture.description}[/]")

    replay_args = argparse.Namespace(
        procedure=fixture.procedure,
        stream=out,
        tau_complete=0.75,
        tau_abstain=0.50,
        strict=False,
        expect_clean=False,
    )
    return cmd_replay(replay_args, console)


def cmd_live(args: argparse.Namespace, console: Console) -> int:
    """Run a supervised session headless -- no dashboard, no browser.

    This is the shape the edge device runs in when nobody is watching a screen:
    camera to bus to engine to hash-chained telemetry. The dashboard is
    ``scripts/web_demo.py``; this is the same supervision without it.
    """
    import uuid
    from datetime import UTC, datetime

    from orbital_har.core.bus import EventBus
    from orbital_har.perception.capture import Camera
    from orbital_har.perception.detect import Detector
    from orbital_har.perception.pipeline import PerceptionPipeline
    from orbital_har.runtime.runner import SessionRunner
    from orbital_har.runtime.session import make_engine, perception_needs, procedure_rows
    from orbital_har.runtime.store import Store
    from orbital_har.runtime.telemetry import TelemetryWriter

    proc = load_procedure(args.procedure, console)
    meta = proc.procedure

    data_root = Path(args.data)
    session_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    session_dir = data_root / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    store = Store(data_root / "sarthi.db")
    rows = procedure_rows(proc)
    store.upsert_procedure(
        id=meta.id,
        name=meta.name,
        version=meta.version,
        vocabulary=meta.vocabulary,
        rack_markers=meta.rack_markers,
        source_path=str(resolve_procedure(args.procedure)),
        yaml_content=json.dumps(rows, sort_keys=True),
        step_count=len(proc.steps),
        steps=rows,
    )
    store.create_session(
        session_id=session_id,
        procedure_id=meta.id,
        procedure_version=meta.version,
        mode="live",
        session_dir=str(session_dir),
        steps_total=len(proc.steps),
    )

    from ultralytics import YOLO

    console.print("[dim]loading detector ...[/]")
    detector = Detector(YOLO(args.model))
    pipeline = PerceptionPipeline(detector)
    want_rack, want_pose = perception_needs(proc)
    pipeline.configure(want_rack=want_rack, want_pose=want_pose, rack_dictionary=meta.rack_markers)

    engine = make_engine(proc)
    bus = EventBus()
    telemetry = TelemetryWriter(session_dir / "telemetry.jsonl").open()
    runner = SessionRunner(session_id, engine, bus, store, telemetry)
    bus.subscribe(lambda ev: _render(ev, console, engine))

    console.rule(f"[bold]{meta.name}[/] [dim]{session_id}[/]")
    console.print(
        f"[dim]rack={'on' if want_rack else 'off'} pose={'on' if want_pose else 'off'} "
        f"camera={args.camera}[/]"
    )
    try:
        runner.start_live(
            pipeline,
            Camera(args.camera),
            wanted=proc.vocabulary_classes,
            min_area=args.min_area,
            max_seconds=args.seconds,
        )
    except KeyboardInterrupt:
        console.print("[dim]interrupted[/]")
    except RuntimeError as exc:
        console.print(f"[bold red]error:[/] {exc}")
        return 2
    finally:
        store.close()

    console.rule("[bold]Summary[/]")
    _render_summary(engine, console)
    console.print(f"[dim]telemetry: {session_dir / 'telemetry.jsonl'}[/]")
    return 0


def _vocabulary(console: Console):
    from orbital_har.datagen.vocabulary import build

    classes = build(directory=PROCEDURE_DIR)
    console.print(
        f"[dim]{len(classes)} detector classes derived from "
        f"{', '.join(sorted({p for c in classes for p in c.procedures}))}[/]"
    )
    return classes


def cmd_dataset(args: argparse.Namespace, console: Console) -> int:
    """Scaffold, pre-label, split and inspect the training corpus."""
    from orbital_har.datagen import dataset as ds

    classes = _vocabulary(console)
    names = [c.name for c in classes]
    root = Path(args.root)

    if args.action == "init":
        path = ds.scaffold(root, names)
        console.print(f"[green]ready[/] {path}")
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("#", justify="right", style="dim")
        table.add_column("class")
        table.add_column("object", style="dim")
        table.add_column("prompt", style="dim")
        for c in classes:
            table.add_row(str(c.index), c.name, c.object_id, c.prompt)
        console.print(table)
        console.print(f"[dim]put footage frames in {root / 'raw'}, then 'dataset label'[/]")
        return 0

    if args.action == "label":
        from orbital_har.datagen.autolabel import AutoLabeller

        target = Path(args.dir) if args.dir else root / "raw"
        if not target.is_dir():
            console.print(f"[bold red]error:[/] {target} does not exist")
            return 2
        console.print(f"[dim]pre-labelling {target} ...[/]")
        stats = AutoLabeller(classes, weights=args.weights, conf=args.conf).label_dir(target)
        console.print(stats.report())
        return 0

    if args.action == "split":
        counts = ds.split_dataset(root, val_frac=args.val_frac, seed=args.seed)
        console.print(
            f"[green]split[/] train={counts['train']} val={counts['val']} "
            "[dim](by clip, so near-duplicate frames cannot straddle the split)[/]"
        )
        return 0

    stats = ds.stats(root, names)
    console.print(stats.report())
    return 0 if stats.ready else 1


def cmd_train(args: argparse.Namespace, console: Console) -> int:
    from orbital_har.datagen.train import TrainConfig, train

    config = TrainConfig(
        data=Path(args.data),
        weights=args.weights,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=Path(args.project),
        name=args.name,
    )
    console.rule("[bold]Training[/]")
    console.print(config.describe())
    if not config.data.exists():
        console.print(f"[bold red]error:[/] {config.data} not found — run 'dataset init'")
        return 2
    best = train(config)
    console.print(f"[green]done[/] {best}")
    return 0


def cmd_eval(args: argparse.Namespace, console: Console) -> int:
    """Score the system: step verdicts, calibration, and optionally the detector.

    The step and calibration halves run on the golden corpus and need no
    footage, so this is answerable today. The detector half needs weights and a
    dataset.
    """
    from orbital_har.datagen.evaluate import (
        Case,
        calibrate,
        calibration_samples,
        evaluate_steps,
    )
    from orbital_har.simkit.fixtures import ALL_FIXTURES
    from orbital_har.simkit.fixtures import build as build_fixture

    cases = []
    for name in sorted(ALL_FIXTURES):
        fixture = build_fixture(name)
        cases.append(
            Case(
                name=name,
                procedure=load_procedure(fixture.procedure, console),
                events=fixture.scenario.events,
                expected_states=fixture.expected.final_states,
                expected_alerts=list(fixture.expected.alerts),
            )
        )

    console.rule("[bold]Step verdicts[/]")
    metrics = evaluate_steps(cases)
    console.print(metrics.report())

    console.rule("[bold]Calibration[/]")
    calibration = calibrate(calibration_samples(cases), target_precision=args.precision)
    console.print(calibration.report())

    detector = None
    if args.weights:
        from orbital_har.datagen.evaluate import evaluate_detector

        console.rule("[bold]Detector[/]")
        detector = evaluate_detector(args.weights, Path(args.data), imgsz=args.imgsz)
        console.print(detector.report())

    if args.save:
        from orbital_har.runtime.store import Store

        store = Store(Path(args.data_root) / "sarthi.db")
        model_id = store.upsert_model(
            name=Path(args.weights).stem if args.weights else "reasoning-only",
            task="detect",
            version=args.version,
            file_path=args.weights or "",
            classes=[c.name for c in _vocabulary(console)],
            metrics={
                "step_accuracy": round(metrics.accuracy, 4),
                "alert_recall": round(metrics.alert_recall, 4),
                "false_alerts_per_10min": round(metrics.false_alerts_per_10min, 3),
                **({"map50_95": detector.map50_95, "map50": detector.map50} if detector else {}),
            },
        )
        store.insert_calibration(
            model_id=model_id,
            temperature=calibration.temperature,
            tau_complete=calibration.tau_complete,
            tau_abstain=calibration.tau_abstain,
            dataset_hash=calibration.dataset_hash or "golden-corpus",
            ece=calibration.ece_after,
        )
        store.close()
        console.print(f"[green]saved[/] model id {model_id} + calibration")

    return 0


def cmd_export(args: argparse.Namespace, console: Console) -> int:
    from orbital_har.datagen.export import export

    result = export(args.weights, fmt=args.format, imgsz=args.imgsz, half=args.half)
    console.print(f"[green]{result.report()}[/]")
    return 0


def cmd_verify(args: argparse.Namespace, console: Console) -> int:
    """Verify a hash-chained telemetry file."""
    from orbital_har.runtime.telemetry import verify

    result = verify(args.file)
    if result.ok:
        console.print(f"[green]OK[/] {result.record_count} records, chain intact")
        return 0
    console.print(f"[bold red]FAILED[/] at seq {result.first_bad_seq}: {result.error}")
    return 1


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _render(event, console: Console, engine: Engine) -> int:
    """Print one verdict event. Returns 1 if it was an alert."""
    p = event.payload
    stamp = f"[dim]{event.t - (engine.started_t or event.t):7.2f}s[/]"

    if event.type == EventType.STEP_STATE.value:
        state = p["state"]
        style = _STATE_STYLE.get(state, "white")
        line = f"{stamp} [{style}]{state.upper():<13}[/] {p['step_id']}  {p['name']}"
        if p.get("duration_s") is not None:
            line += f"  [dim]{p['duration_s']}s[/]"
        if p.get("confidence"):
            line += f"  [dim]conf {p['confidence']:.2f}[/]"
        console.print(line)
        if state == StepState.ACTIVE.value:
            console.print(f'{" " * 11}[cyan]->[/] "{p["voice"]}"')
        return 0

    if event.type == EventType.ALERT.value:
        style = _SEVERITY_STYLE.get(p["severity"], "yellow")
        console.print(f"{stamp} [{style}]ALERT {p['kind']}[/]  {p['message']}")
        return 1

    return 0


def _render_summary(engine: Engine, console: Console) -> None:
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("step")
    table.add_column("name")
    table.add_column("state")
    table.add_column("dur", justify="right", style="dim")
    for rt in engine.runtimes:
        style = _STATE_STYLE.get(rt.state.value, "white")
        table.add_row(
            rt.step.id,
            rt.step.name,
            f"[{style}]{rt.state.value}[/]",
            f"{rt.duration_s}s" if rt.duration_s is not None else "",
        )
    console.print(table)

    counts = engine.summary()["counts"]
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    console.print(f"[dim]{'  '.join(parts)}[/]")

    nxt = engine.next_step
    if nxt is not None:
        console.print(f"[cyan]next:[/] {nxt.step.name}")
    else:
        console.print("[green]procedure complete[/]")


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orbital-har",
        description="On-board procedure supervision for BAS experiments (SIH26174).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a procedure definition")
    v.add_argument("procedure", help="path or shorthand, e.g. proc_a")
    v.set_defaults(func=cmd_validate)

    f = sub.add_parser("fixture", help="generate a golden fixture stream")
    f.add_argument("name", help="fixture name, or 'list'")
    f.add_argument("--out", help="output path")
    f.set_defaults(func=cmd_fixture)

    r = sub.add_parser("replay", help="replay an event stream through the engine")
    r.add_argument("--procedure", required=True)
    r.add_argument("--stream", required=True)
    r.add_argument("--tau-complete", type=float, default=0.75, dest="tau_complete")
    r.add_argument("--tau-abstain", type=float, default=0.50, dest="tau_abstain")
    r.add_argument("--strict", action="store_true", help="gate completion on preconditions")
    r.add_argument("--expect-clean", action="store_true", help="exit 1 if any alert fires")
    r.set_defaults(func=cmd_replay)

    d = sub.add_parser("demo", help="build a fixture and replay it")
    d.add_argument("name")
    d.set_defaults(func=cmd_demo)

    live = sub.add_parser("live", help="run a supervised session headless (no dashboard)")
    live.add_argument("procedure", help="path or shorthand, e.g. proc_a")
    live.add_argument("--camera", type=int, default=0)
    live.add_argument("--model", default="yolo11n.pt")
    live.add_argument("--min-area", type=float, default=0.06, dest="min_area")
    live.add_argument("--data", default="data", help="data root")
    live.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="stop after N seconds (default: until the camera stops)",
    )
    live.set_defaults(func=cmd_live)

    ds = sub.add_parser("dataset", help="build and inspect the training corpus")
    ds.add_argument("action", choices=["init", "label", "split", "stats"])
    ds.add_argument("--root", default="datasets/bas", help="dataset root")
    ds.add_argument("--dir", default=None, help="folder to label (default: <root>/raw)")
    ds.add_argument(
        "--weights",
        default="yolov8s-worldv2.pt",
        help="open-vocabulary weights used for pre-labelling",
    )
    ds.add_argument(
        "--conf", type=float, default=0.15, help="pre-label confidence floor; low on purpose"
    )
    ds.add_argument("--val-frac", type=float, default=0.2, dest="val_frac")
    ds.add_argument("--seed", type=int, default=0)
    ds.set_defaults(func=cmd_dataset)

    tr = sub.add_parser("train", help="train the BAS-prop detector")
    tr.add_argument("--data", default="datasets/bas/data.yaml")
    tr.add_argument("--weights", default="yolo11s.pt")
    tr.add_argument("--epochs", type=int, default=100)
    tr.add_argument("--imgsz", type=int, default=640)
    tr.add_argument("--batch", type=int, default=-1)
    tr.add_argument("--device", default=None)
    tr.add_argument("--project", default="runs")
    tr.add_argument("--name", default="bas")
    tr.set_defaults(func=cmd_train)

    ev = sub.add_parser("eval", help="score step verdicts, calibration and the detector")
    ev.add_argument("--weights", default=None, help="detector weights (optional)")
    ev.add_argument("--data", default="datasets/bas/data.yaml")
    ev.add_argument("--imgsz", type=int, default=640)
    ev.add_argument(
        "--precision",
        type=float,
        default=0.95,
        help="target precision that tau_complete must reach",
    )
    ev.add_argument(
        "--save", action="store_true", help="write the model and its calibration to the store"
    )
    ev.add_argument("--data-root", default="data", dest="data_root")
    ev.add_argument("--version", default="v1")
    ev.set_defaults(func=cmd_eval)

    ex = sub.add_parser("export", help="export trained weights for the edge target")
    ex.add_argument("weights")
    ex.add_argument(
        "--format", default="onnx", choices=["onnx", "engine", "torchscript", "openvino"]
    )
    ex.add_argument("--imgsz", type=int, default=640)
    ex.add_argument("--half", action="store_true")
    ex.set_defaults(func=cmd_export)

    vf = sub.add_parser("verify", help="verify a hash-chained telemetry file")
    vf.add_argument("file", help="path to telemetry.jsonl")
    vf.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    console = Console()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args, console))
    except (ProcedureError, KeyError, ValueError) as exc:
        console.print(f"[bold red]error:[/] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
