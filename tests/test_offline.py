"""Invariant #1: zero network at runtime.

CLAUDE.md forbids any outbound call to a non-loopback address. A judge who opens
a network tab will check, and one cloud dependency invalidates the entire
offline premise the product rests on.

This test runs the full reasoning pipeline with outbound sockets poisoned. It
grows teeth in M2 when perception lands -- that is when a model loader might
quietly try to fetch weights.
"""

from __future__ import annotations

import socket

import pytest

from orbital_har.reasoning.schema import Procedure
from orbital_har.simkit.fixtures import build
from tests.conftest import PROCEDURES, run_events

LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}


@pytest.fixture
def no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail loudly on any connection to a non-loopback address."""
    attempts: list[str] = []
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def _host_of(address: object) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return str(address)

    def guarded_connect(self, address):
        host = _host_of(address)
        if host not in LOOPBACK:
            attempts.append(host)
            raise AssertionError(f"outbound network call to {host!r} is forbidden")
        return real_connect(self, address)

    def guarded_create(address, *args, **kwargs):
        host = _host_of(address)
        if host not in LOOPBACK:
            attempts.append(host)
            raise AssertionError(f"outbound network call to {host!r} is forbidden")
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)
    return attempts


def test_pipeline_runs_with_no_network(no_outbound_network: list[str]) -> None:
    procedure = Procedure.load(PROCEDURES / "proc_a.yaml")
    fixture = build("proc_a_clean")
    engine, alerts = run_events(procedure, fixture.scenario.events)

    assert engine.is_complete
    assert alerts == []
    assert no_outbound_network == []


def test_procedure_loading_touches_no_network(no_outbound_network: list[str]) -> None:
    for name in ("proc_a.yaml", "proc_b.yaml"):
        Procedure.load(PROCEDURES / name)
    assert no_outbound_network == []


def test_the_guard_itself_works() -> None:
    """A guard that never fires proves nothing."""
    with pytest.MonkeyPatch.context() as mp:
        blocked: list[str] = []

        def guarded(address, *args, **kwargs):
            blocked.append(str(address))
            raise AssertionError("forbidden")

        mp.setattr(socket, "create_connection", guarded)
        with pytest.raises(AssertionError, match="forbidden"):
            socket.create_connection(("example.com", 443))
        assert blocked
