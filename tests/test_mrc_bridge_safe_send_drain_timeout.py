"""Regression test for mrc/bridge/main.py's BridgeApp._safe_send().

A 2026-10 audit flagged (but couldn't confirm either way) whether
aiohttp's WebSocketResponse.send_json() can block indefinitely if a
client stops reading without closing its socket -- the same shape as
the already-fixed raw StreamWriter.drain() hang this bridge's
_send_mrc_tcp_payload() counterpart protects against. _safe_send()'s
bare `except Exception: pass` would not catch a true hang (no
exception is raised while blocked), so this test confirms the
defensive asyncio.wait_for() wrapper added around the send actually
bounds it, regardless of whether the underlying aiohttp risk is real.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mrc.bridge.main import BridgeApp  # noqa: E402


class _FakeMRCConnection:
    def __init__(self):
        self.sent = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)


class _HangingWebSocket:
    """A ws whose send_json() never returns, simulating a client that
    stopped reading without closing the socket."""

    def __init__(self):
        self.closed = False

    async def send_json(self, payload):
        await asyncio.Event().wait()  # never set -- hangs forever

    async def close(self):
        self.closed = True


class _NormalWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def _make_bridge(tmp_path, **overrides) -> BridgeApp:
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
        "mrc_drain_timeout_seconds": 0.2,
    }
    config.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


class SafeSendDrainTimeoutTests(unittest.IsolatedAsyncioTestCase):

    async def test_hanging_websocket_send_is_bounded_and_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = await _make_bridge(Path(tmp))
            ws = _HangingWebSocket()
            await asyncio.wait_for(
                app._safe_send(ws, {"type": "ping"}), timeout=2.0)
            self.assertTrue(ws.closed,
                             "a hung send_json() should be timed out "
                             "and the socket closed, not left hanging")

    async def test_normal_websocket_send_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = await _make_bridge(Path(tmp))
            ws = _NormalWebSocket()
            await asyncio.wait_for(
                app._safe_send(ws, {"type": "ping"}), timeout=2.0)
            self.assertEqual(ws.sent, [{"type": "ping"}])


if __name__ == '__main__':
    unittest.main()
