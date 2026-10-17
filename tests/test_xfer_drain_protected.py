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

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

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


class ProcStdinDrainTimeoutWrapTests(unittest.TestCase):
    """Follow-up audit finding: send_file()'s and recv_file()'s own
    _session_to_proc() pumps call `await proc.stdin.drain()` raw --
    unlike every socket-writer drain() in this file (see the module
    docstring above), a stuck DOS protocol subprocess that stops
    reading its stdin without exiting could hang this call forever.
    _session_to_proc() is a nested closure inside send_file()/
    recv_file() that isn't independently importable and drives a real
    subprocess pipeline end to end -- rather than reproducing that
    whole pipeline here, this inspects the actual source text to
    confirm each of the two `proc.stdin.drain()` call sites is now
    genuinely wrapped in `asyncio.wait_for(..., timeout=
    WRITE_DRAIN_TIMEOUT_SECONDS)`, which is directly sensitive to the
    real fix (reverting it removes the wrapper, failing this test)."""

    @classmethod
    def setUpClass(cls):
        cls.source = (_REPO_ROOT / 'anetbbs' / 'features' / 'xfer.py').read_text()

    def _assert_wrapped(self, marker_count_label, after_marker):
        idx = 0
        found = 0
        while True:
            idx = self.source.find('proc.stdin.write(clean)', idx)
            if idx == -1:
                break
            window = self.source[idx:idx + 300]
            self.assertIn(
                'asyncio.wait_for(', window,
                f'{after_marker}: proc.stdin.drain() must be wrapped in '
                f'asyncio.wait_for(...)')
            self.assertIn(
                'proc.stdin.drain()', window,
                f'{after_marker}: expected proc.stdin.drain() inside the wait_for')
            self.assertIn(
                'WRITE_DRAIN_TIMEOUT_SECONDS', window,
                f'{after_marker}: expected the shared drain-timeout constant')
            found += 1
            idx += 1
        self.assertEqual(found, 2, 'expected exactly 2 proc.stdin.write(clean) '
                         'sites (send_file() and recv_file()\'s own '
                         '_session_to_proc() pumps), each immediately '
                         'followed by a timeout-wrapped drain()')

    def test_both_session_to_proc_drains_are_timeout_wrapped(self):
        self._assert_wrapped('both sites', 'proc.stdin.write(clean)')


if __name__ == '__main__':
    unittest.main()
