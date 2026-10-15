"""Regression test for a real gap found in a follow-up security audit:
anetbbs/features/xfer.py's 3 session.writer.drain() call sites
(_negotiate_binary(), and send_file()/recv_file()'s own
_proc_to_session() pumps) called session.writer.drain() raw, bypassing
BBSSession._drain_protected() -- the timeout-wrapped helper that
exists specifically so a stuck client (one that stops reading without
closing the socket) can't hang a drain() call forever.

The overall transfer was already loosely bounded by send_file()'s/
recv_file()'s own 300s asyncio.wait_for(proc.wait(), ...), but a hung
drain() here still stalled the transfer up to that full 5 minutes
instead of ending promptly -- this closes that gap by routing through
the real protection instead of a raw drain().
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.xfer import _negotiate_binary


class _FakeSession:
    def __init__(self):
        self.writer = mock.Mock()
        self.writer.write = mock.Mock()
        self._drain_protected_calls = 0

    async def _drain_protected(self):
        self._drain_protected_calls += 1


class XferDrainProtectedTests(unittest.TestCase):
    def test_negotiate_binary_routes_through_drain_protected(self):
        session = _FakeSession()

        asyncio.run(_negotiate_binary(session, enable=True))

        self.assertTrue(session.writer.write.called)
        self.assertEqual(session._drain_protected_calls, 1,
                         '_negotiate_binary() must call the timeout-'
                         'protected drain helper, not a raw '
                         'session.writer.drain()')

    def test_negotiate_binary_disable_also_routes_through_drain_protected(self):
        session = _FakeSession()

        asyncio.run(_negotiate_binary(session, enable=False))

        self.assertEqual(session._drain_protected_calls, 1)

    def test_a_hanging_drain_protected_does_not_crash_or_hang_the_caller(self):
        """_negotiate_binary() wraps its body in a broad try/except --
        confirm a real CarrierLost raised by _drain_protected() (what
        the real timeout path raises) is swallowed as best-effort,
        same as any other connection failure here."""
        from anetbbs.core.session import CarrierLost

        session = _FakeSession()

        async def _raising_drain():
            raise CarrierLost('drain timed out')
        session._drain_protected = _raising_drain

        asyncio.run(asyncio.wait_for(
            _negotiate_binary(session, enable=True), timeout=5))
        # No exception propagated -- best-effort semantics preserved.


if __name__ == '__main__':
    unittest.main()
