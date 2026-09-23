"""Session orchestration layer.

Binds the reasoning Engine to the event Bus, and persists the resulting state
transitions to the Store and Telemetry logs, while broadcasting to the UI.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from orbital_har.core.bus import EventBus
from orbital_har.core.types import Event, EventType
from orbital_har.reasoning.engine import Engine
from orbital_har.runtime.store import Store
from orbital_har.runtime.telemetry import TelemetryWriter


class SessionRunner:
    """Orchestrates an active procedure session."""

    def __init__(
        self,
        session_id: str,
        engine: Engine,
        bus: EventBus,
        store: Store,
        telemetry: TelemetryWriter,
        ws_publisher: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.engine = engine
        self.bus = bus
        self.store = store
        self.telemetry = telemetry
        self.ws_publisher = ws_publisher

        # Bind the engine to the bus so it publishes verdicts directly onto it.
        # This fulfills the rule: "Pass bus into Engine".
        self.engine.bus = self.bus

        # Subscribe to the bus to consume events for persistence and broadcast.
        self.bus.subscribe(self._on_bus_event)

    def _on_bus_event(self, event: Event) -> None:
        """Handle events published to the bus.
        
        The Engine processes perception events and emits step_state / alert events.
        We only persist these lifecycle/transition/alert events to SQLite and Telemetry.
        """
        # Broadcast to UI
        if self.ws_publisher is not None:
            # We can convert event to dict for JSON serialization by the publisher
            self.ws_publisher(dataclasses.asdict(event))

        # Persist to Store and Telemetry
        iso_time = datetime.fromtimestamp(event.t, UTC).isoformat()
        if event.type == EventType.STEP_STATE.value:
            payload = event.payload
            self.store.upsert_step_run(
                session_id=self.session_id,
                step_id=payload["step_id"],
                ordinal=payload["ordinal"],
                state=payload["state"],
                confidence=payload.get("confidence"),
                evidence=payload.get("evidence"),
                reason=payload.get("reason"),
            )
            self.telemetry.write(event.type, payload, t=iso_time)

        elif event.type == EventType.ALERT.value:
            payload = event.payload
            self.store.insert_alert(
                session_id=self.session_id,
                bus_seq=event.seq,
                kind=payload["kind"],
                severity=payload["severity"],
                message=payload["message"],
                step_id=payload.get("step_id"),
                expected_step_id=payload.get("expected_step_id"),
            )
            self.telemetry.write(event.type, payload, t=iso_time)

        elif event.type == "session_start" or event.type == "session_end":
            self.telemetry.write(event.type, event.payload, t=iso_time)

    def replay(self, stream: Iterator[Event]) -> None:
        """Run a recorded stream through the engine synchronously."""
        for event in stream:
            # Publish observations once; feed them to the engine once.
            # Engine verdicts are emitted through the same bus.
            self.bus.publish(event)
            self.engine.on_event(event)
            
        # Flush the engine at the end of the stream
        self.engine.flush()
        
        self._finalize_session()

    def start_live(self) -> None:
        """Start a live session."""
        pass # To be implemented when live perception is added.

    def _finalize_session(self) -> None:
        """Update the telemetry chain metadata in the store when done."""
        summary = self.engine.summary()
        counts = summary["counts"]
        
        self.telemetry.write("session_end", {"summary": summary}, t=datetime.now(UTC).isoformat())
        self.telemetry.close()
        
        self.store.close_session(
            session_id=self.session_id,
            status="complete" if summary["complete"] else "aborted",
            steps_complete=counts.get("complete", 0),
            steps_skipped=counts.get("skipped", 0),
            steps_out_of_order=counts.get("out_of_order", 0),
            steps_unverified=counts.get("unverified", 0),
            steps_overridden=counts.get("overridden", 0),
            alert_count=sum(1 for _ in self.store.get_alerts(self.session_id)),
        )
        
        self.store.upsert_telemetry_chain(
            session_id=self.session_id,
            genesis_hash=self.telemetry.genesis_hash,
            last_seq=self.telemetry.last_seq,
            last_hash=self.telemetry.last_hash,
            record_count=self.telemetry.record_count,
            bytes_written=self.telemetry.bytes_written,
        )

    def close(self) -> None:
        """Close resources."""
        self.telemetry.close()
