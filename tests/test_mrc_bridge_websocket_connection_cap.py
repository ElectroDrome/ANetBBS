"""Regression tests for a real Medium finding from a follow-up security
audit: handle_websocket() (the /ws and /mrcws routes -- every web MRC
user and the terminal MRC door both go through this path) tracked every
accepted connection in self.websockets with NO ceiling at all, unlike
handle_mrc_tcp_connection's own MRC_TCP_MAX_CONNECTIONS/_PER_IP caps
(added in an earlier audit round). Since this is the one shared
multiplexer carrying every web/terminal user's chat traffic, unbounded
concurrent connections from a single account could exhaust the bridge
process for everyone on that transport.

Exercises a real aiohttp server/client (aiohttp.test_utils.TestServer/
TestClient) rather than mocking the WebSocket layer, matching this
project's standing preference for verifying real behavior over
reasoning through a diff.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mrc.bridge.main import BridgeApp  # noqa: E402


class _FakeMRCConnection:
    def __init__(self):
        self.sent = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)


async def _make_bridge(tmp_path, **overrides) -> BridgeApp:
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
        "ws_disconnect_grace_seconds": 0,
        "join_packet_delay_ms": 0,
        "request_banners_on_join": False,
        "request_motd_on_join": False,
        "announce_join_part": True,
    }
    config.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


class _WsBridgeHarness(unittest.IsolatedAsyncioTestCase):
    """Shared real-loopback aiohttp server setup for every test below."""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.bridge = await _make_bridge(self.tmp_path)
        web_app = web.Application()
        web_app.router.add_get("/ws", self.bridge.handle_websocket)
        self.server = TestServer(web_app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmpdir.cleanup()


class WebSocketConnectionCapTests(_WsBridgeHarness):

    async def test_normal_single_connection_still_works(self):
        """Sanity check: the cap must not false-positive on ordinary
        single-connection traffic."""
        ws = await self.client.ws_connect("/ws")
        try:
            welcome = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            self.assertEqual(welcome.get("type"), "welcome")
            self.assertEqual(len(self.bridge.websockets), 1)
        finally:
            await ws.close()

    async def test_global_cap_rejects_connections_beyond_the_limit(self):
        with mock.patch("mrc.bridge.main.MRC_WS_MAX_CONNECTIONS", 3):
            conns = []
            for _ in range(3):
                ws = await self.client.ws_connect("/ws")
                await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                conns.append(ws)

            self.assertEqual(len(self.bridge.websockets), 3)

            # The 4th attempt must be refused at the HTTP-upgrade step
            # itself (a plain 503, never a successful 101 upgrade), so
            # the client's ws_connect() raises instead of succeeding.
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await self.client.ws_connect("/ws")

            self.assertEqual(len(self.bridge.websockets), 3,
                              'the rejected connection must never be '
                              'added to the tracked-websockets dict')

            for ws in conns:
                await ws.close()

    async def test_per_ip_cap_rejects_connections_beyond_the_limit(self):
        # Every connection from this test client originates from the
        # same loopback address, so the per-IP cap is what's actually
        # exercised here -- the global cap is left high enough to not
        # interfere.
        with mock.patch("mrc.bridge.main.MRC_WS_MAX_CONNECTIONS", 100), \
             mock.patch("mrc.bridge.main.MRC_WS_MAX_CONNECTIONS_PER_IP", 2):
            conns = []
            for _ in range(2):
                ws = await self.client.ws_connect("/ws")
                await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                conns.append(ws)

            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await self.client.ws_connect("/ws")

            self.assertEqual(len(self.bridge.websockets), 2,
                              'a 3rd connection from the same IP, over '
                              'the per-IP cap, must be refused')

            for ws in conns:
                await ws.close()


if __name__ == "__main__":
    unittest.main()
