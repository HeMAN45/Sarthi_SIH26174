"""Command line entry point.

M0 scope: validate procedures, generate fixture streams, and replay a stream
through the engine printing verdicts. No camera, no models, no dashboard --
that is the point. If this works, the spine of the system is sound.
"""

from __future__ import annotations

import argparse
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
