"""The event bus -- the seam between perception and reasoning.

Everything perception observes is published here; everything reasoning knows it
learned here. That separation is what makes replay, parallel development and
demo insurance possible (docs/02-TRD.md section 4.3).

Invariant: nothing in this module may import from ``perception`` or
``reasoning``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from orbital_har.core.types import Event

Handler = Callable[[Event], None]


class JsonlSink:
    """Append-only event stream writer.

    fsyncs on close only; the per-frame path must not block on disk. Crash
    tolerance comes from the file being append-only, not from durability of the
    last few records.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self.count = 0

    def write(self, event: Event) -> None:
        self._fh.write(event.to_json())
        self._fh.write("\n")
        self.count += 1

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class EventBus:
    """Synchronous publish/subscribe with an optional persistent sink.

    Synchronous is deliberate for now: the ordering guarantees are free and the
    handler count is small. If a handler ever becomes slow enough to matter it
    gets its own queue -- capture must never block on a consumer.
    """

    def __init__(self, sink: JsonlSink | None = None) -> None:
        self._handlers: list[Handler] = []
        self._sink = sink
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def emit(self, src: str, type_: str, payload: dict[str, Any], t: float, v: int = 1) -> Event:
        """Build, number and publish an event in one call."""
        self._seq += 1
        event = Event(t=t, seq=self._seq, src=src, type=type_, payload=payload, v=v)
        self.publish(event)
        return event

    def publish(self, event: Event) -> None:
        """Publish a pre-built event without renumbering it.

        Replay uses this so recorded sequence numbers survive intact.
        """
        if event.seq > self._seq:
            self._seq = event.seq
        if self._sink is not None:
            self._sink.write(event)
        for handler in self._handlers:
            handler(event)

    def close(self) -> None:
        if self._sink is not None:
            self._sink.close()


def read_stream(path: str | Path) -> Iterator[Event]:
    """Yield events from a recorded stream, skipping blank lines.

    Raises on a malformed line rather than silently dropping it: a corrupt
    stream during replay is a bug worth surfacing, not smoothing over.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Event.from_json(line)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                raise ValueError(f"{p}:{lineno}: malformed event: {exc}") from exc
