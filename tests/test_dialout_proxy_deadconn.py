"""Regression tests for a real gap found in a follow-up security audit:
anetbbs/features/dialout.py's DialoutMenu._proxy() (the bidirectional
telnet bridge a logged-in user opens to another BBS) had NO idle-read
timeout on the remote side and NO timeout on any of its drain() calls
-- unlike anetbbs/games/door_runner.py's rlogin/telnet door bridges,
which already got this exact fix after a real live freeze report.

A remote BBS that stalls (network blip, remote busy/hung, or one that
accepts the connection then simply never reads/writes without closing
the socket) froze the user's ENTIRE session permanently -- nothing
ever unblocked remote_reader.read() or a hung drain(). See the
deadconn-freeze-audit skill for the full methodology this follows.

Per door_runner.py's own established, deliberate convention: the idle
timeout applies ONLY to the remote-reading direction
(remote_to_user()), never to the user's own keystroke-reading
direction (user_to_remote()) -- a legitimately idle user who just
isn't typing is normal and must not be disconnected.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _deadconn_fixtures import FakeWriter, make_session, EofReader, run

from anetbbs.features.dialout import DialoutMenu
import anetbbs.features.dialout as dialout_mod
import anetbbs.core.session as session_mod


class _HangingReader:
    """Never returns -- simulates a remote peer that accepts the
    connection then goes completely silent without closing it."""
    async def read(self, n=4096):
        await asyncio.sleep(3600)


class _HangingWriter:
    """write() succeeds immediately (buffers locally, like a real
    socket under backpressure); drain() never completes -- simulates a
    remote peer that stopped reading without closing the socket."""
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        await asyncio.sleep(3600)


class _EofRemoteReader:
    """Clean remote EOF on the very first read -- the normal "remote
    closed the connection" case, used to confirm the happy path still
    works unchanged after the fix."""
    async def read(self, n=4096):
        return b''


class DialoutProxyIdleTimeoutTests(unittest.TestCase):
    def test_hanging_remote_read_does_not_freeze_the_session_forever(self):
        """The core bug: a remote that goes silent (no close, no data)
        used to hang _proxy() -- and therefore the whole user
        session -- forever. Confirmed bounded here by patching
        DIALOUT_IDLE_TIMEOUT down to something a test can wait out."""
        session, writer = make_session(EofReader())
        menu = DialoutMenu(session)

        with mock.patch.object(dialout_mod, 'DIALOUT_IDLE_TIMEOUT', 1):
            run(menu._proxy(_HangingReader(), FakeWriter()), timeout=10)
        # Reaching here at all (run() didn't itself time out) is the
        # regression test -- the old code would hang past `run`'s
        # own 10s bound, failing the test instead of passing silently.

    def test_clean_remote_eof_still_works_unchanged(self):
        """Happy path: remote closes normally -- must return promptly,
        not wait out the idle timeout."""
        session, writer = make_session(EofReader())
        menu = DialoutMenu(session)

        run(menu._proxy(_EofRemoteReader(), FakeWriter()), timeout=5)
        self.assertIn(b'Returned to ANetBBS', bytes(writer.written))


class DialoutProxyDrainTimeoutTests(unittest.TestCase):
    def test_hanging_remote_drain_does_not_freeze_the_session_forever(self):
        """The other half of the bug: a remote that accepts bytes
        (write() succeeds) but never actually reads them off the wire
        (drain() never completes) used to hang the up-front telnet
        negotiation forever, before the proxy loops even started."""
        session, writer = make_session(EofReader())
        menu = DialoutMenu(session)

        with mock.patch.object(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 1):
            run(menu._proxy(_EofRemoteReader(), _HangingWriter()), timeout=10)


if __name__ == '__main__':
    unittest.main()
