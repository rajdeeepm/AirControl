from __future__ import annotations

import asyncio
import json
import socket
import threading

import pytest

from aircontrol.ipc import (
    FakeTransport,
    IpcClient,
    IpcProtocolError,
    IpcServer,
    action_event,
    candidate_event,
    metrics_event,
    parse_command,
    status_event,
)


def test_status_event_has_v1_schema() -> None:
    event = status_event(
        armed=True,
        raw_pose="open_palm",
        active_pose="open_palm",
        hold_progress=0.75,
        hand_visible=True,
        status_text="Ready",
    )

    assert event == {
        "v": 1,
        "type": "status",
        "armed": True,
        "raw_pose": "open_palm",
        "active_pose": "open_palm",
        "hold_progress": 0.75,
        "hand_visible": True,
        "status_text": "Ready",
    }


def test_action_candidate_and_metrics_events_have_v1_schema() -> None:
    action = action_event(
        kind="volume_up",
        confidence=0.95,
        description="Raise volume",
        ts=10.0,
    )
    candidate = candidate_event(
        gate="fire",
        reason="thresholds_passed",
        confidence=0.95,
        ts=10.0,
    )
    metrics = metrics_event(
        candidates_per_hour=3.0,
        fp_per_hour=0.1,
        latency_ms_p50=42.0,
        cpu_pct=8.5,
        ts=10.0,
    )

    assert action == {
        "v": 1,
        "type": "action",
        "kind": "volume_up",
        "category": "hotkey",
        "confidence": 0.95,
        "description": "Raise volume",
        "ts": 10.0,
    }
    assert candidate == {
        "v": 1,
        "type": "candidate",
        "gate": "fire",
        "reason": "thresholds_passed",
        "confidence": 0.95,
        "ts": 10.0,
    }
    assert metrics == {
        "v": 1,
        "type": "metrics",
        "candidates_per_hour": 3.0,
        "fp_per_hour": 0.1,
        "latency_ms_p50": 42.0,
        "cpu_pct": 8.5,
        "ts": 10.0,
    }


def test_parse_command_accepts_valid_command() -> None:
    raw = json.dumps({"v": 1, "type": "command", "name": "toggle_arm"})

    assert parse_command(raw) == {
        "v": 1,
        "type": "command",
        "name": "toggle_arm",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 2, "type": "command", "name": "toggle_arm"},
        {"v": 1, "type": "command"},
    ],
)
def test_parse_command_rejects_wrong_version_or_missing_name(
    payload: dict[str, object],
) -> None:
    with pytest.raises(IpcProtocolError):
        parse_command(json.dumps(payload))


def test_fake_transport_delivers_broadcasts_to_subscribers() -> None:
    transport = FakeTransport()
    received: list[dict[str, object]] = []
    transport.subscribe(received.append)
    event = action_event(
        kind="click",
        confidence=1.0,
        description="Primary click",
        ts=1.0,
    )

    transport.broadcast(event)

    assert received == [event]


def test_fake_transport_routes_commands_to_callback() -> None:
    transport = FakeTransport()
    received: list[dict[str, object]] = []
    transport.on_command(received.append)

    transport.send_command("pause")

    assert received == [{"v": 1, "type": "command", "name": "pause"}]


def test_real_socket_round_trip() -> None:
    pytest.importorskip("websockets")
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("test environment forbids loopback sockets")
    else:
        probe.close()

    commands: list[dict[str, object]] = []
    command_received = threading.Event()
    server = IpcServer(host="127.0.0.1", port=0)

    def receive_command(command: dict[str, object]) -> None:
        commands.append(command)
        command_received.set()

    server.on_command(receive_command)
    server.start()

    async def exercise_socket() -> None:
        client = IpcClient(f"ws://127.0.0.1:{server.port}")
        try:
            await client.send_command("get_status")
            assert await asyncio.to_thread(command_received.wait, 2.0)
            event = status_event(
                armed=False,
                raw_pose="none",
                active_pose="none",
                hold_progress=0.0,
                hand_visible=False,
                status_text="Paused",
            )
            server.broadcast(event)
            assert await asyncio.wait_for(anext(client), timeout=2.0) == event
        finally:
            await client.close()

    try:
        asyncio.run(exercise_socket())
    finally:
        server.stop()

    assert commands == [{"v": 1, "type": "command", "name": "get_status"}]
