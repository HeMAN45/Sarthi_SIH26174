"""Launcher for the SARTHI dashboard.

Everything this used to contain now lives in the package, where the
architecture contract applies to it:

    orbital_har.perception.capture     camera
    orbital_har.perception.detect      detector / state classifier
    orbital_har.perception.pipeline    rack, pose, hands, contact -> events
    orbital_har.runtime.session        LiveSession, SessionLog
    orbital_har.runtime.training       on-device classifier training
    orbital_har.runtime.videoout       mp4 + RTSP
    orbital_har.server.app             the HTTP/WebSocket surface

This file is now only argument parsing and wiring. That matters: the
import-linter contract covers ``orbital_har`` and nothing else, so perception
code living in ``scripts/`` was perception code outside the boundary it was
supposed to obey.

Run:
    uv run python scripts/web_demo.py --procedure proc_a
then open http://localhost:8000
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from orbital_har.perception.detect import Detector
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.session import LiveSession
from orbital_har.runtime.training import TrainManager
from orbital_har.server import app as server_app


def _load_detector(args: argparse.Namespace) -> Detector:
    if args.world:
        from ultralytics import YOLOWorld

        print("[web] loading YOLO-World open-vocabulary detector ...")
        return Detector(YOLOWorld(args.world_model), open_vocab=True)

    from ultralytics import YOLO

    print("[web] loading detector ...")
    return Detector(YOLO(args.model))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="SARTHI live dashboard")
    ap.add_argument("--procedure", default="demo_live")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument(
        "--world",
        action="store_true",
        help="use the YOLO-World open-vocabulary detector (any typed object)",
    )
    ap.add_argument("--world-model", default="yolov8s-worldv2.pt")
    ap.add_argument("--min-area", type=float, default=0.06)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument(
        "--host", default="127.0.0.1", help="0.0.0.0 makes the live feed reachable from the network"
    )
    ap.add_argument(
        "--rtsp", default=None, help="RTSP URL to publish to, e.g. rtsp://192.168.1.50:8554/live"
    )
    ap.add_argument("--data", default="data", help="data root")
    # PS bullet 5 requires storing the video locally, so recording is ON by
    # default. The storage cost is handled by downscaling, not by disabling it.
    ap.add_argument(
        "--no-record", action="store_true", help="disable local mp4 recording (on by default)"
    )
    ap.add_argument(
        "--record-height",
        type=int,
        default=360,
        help="recording height in px; width follows the aspect (default 360)",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    proc_path = Path(args.procedure)
    if not proc_path.exists():
        proc_path = Path("procedures") / f"{args.procedure}.yaml"
    procedure = Procedure.load(proc_path)

    data_root = Path(args.data)
    session = LiveSession(
        procedure,
        _load_detector(args),
        camera=args.camera,
        min_area=args.min_area,
        data_root=data_root,
        rtsp_url=args.rtsp,
        record=not args.no_record,
        record_height=args.record_height,
    )

    # Any session still marked 'running' belongs to a previous hard kill.
    # Retire it before starting, so the Sessions list reflects reality instead
    # of accumulating phantom runs that never ended.
    recovered = session.recover_orphans()
    if recovered:
        print(f"[web] recovered {recovered} interrupted session(s) from a previous run")

    session.start()

    # uvicorn.run() hands back no server object, so /api/shutdown would have
    # nothing to ask. Build the Server ourselves and keep the handle.
    server = uvicorn.Server(
        uvicorn.Config(server_app.app, host=args.host, port=args.port, log_level="warning")
    )
    server_app.configure(
        session.store,
        session=session,
        trainer=TrainManager(data_root / "custom"),
        server=server,
    )

    print(f"[web] open  http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        print(f"[web] live feed on the network at http://<this-machine-ip>:{args.port}/video")
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
