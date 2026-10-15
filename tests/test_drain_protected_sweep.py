"""Regression tests for two small, low-severity findings from a
follow-up security audit sweep of every raw session.writer.drain() /
writer.drain() call site in the codebase: features/login_modules.py's
_run_shell() (writes a sysop-configured login-module's shell output to
the terminal) and core/rlogin_server.py's handshake acknowledgement
byte both called drain() raw instead of going through the existing
timeout-protected path -- found alongside the higher-severity
dialout.py/door_runner.py/xfer.py gaps the same sweep caught.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass


class LoginModulesShellDrainProtectedTests(unittest.TestCase):
    def test_shell_module_output_routes_through_drain_protected(self):
        from anetbbs.features.login_modules import _run_shell

        class _FakeSession:
            def __init__(self):
                self.user = {'username': 'tester'}
                self.writer = _FakeWriter()
                self.drain_protected_calls = 0

            async def _drain_protected(self):
                self.drain_protected_calls += 1

        session = _FakeSession()

        async def _fake_create_subprocess_shell(*args, **kwargs):
            class _Proc:
                pid = 12345
                async def communicate(self):
                    return (b'hello from shell module\n', None)
            return _Proc()

        with mock.patch('asyncio.create_subprocess_shell',
                        _fake_create_subprocess_shell):
            asyncio.run(_run_shell(session, {'command': 'echo hi'}))

        self.assertEqual(session.drain_protected_calls, 1,
                         '_run_shell() must call the timeout-protected '
                         'drain helper, not a raw session.writer.drain()')
        self.assertIn(b'hello from shell module', bytes(session.writer.written))


class RloginHandshakeAckDrainProtectedTests(unittest.TestCase):
    def test_handshake_ack_drain_is_timeout_wrapped(self):
        """Confirms the fix via source inspection of the actual
        awaited coroutine shape (asyncio.wait_for(writer.drain(),
        timeout=...)), since driving a real rlogin handshake end-to-end
        needs much heavier connection scaffolding than this one-line
        fix warrants -- the existing test_rlogin_handshake_timeout.py
        already covers the surrounding handshake-read timeout
        behavior this ack write now matches."""
        import inspect
        from anetbbs.core import rlogin_server

        src = inspect.getsource(rlogin_server.RloginServer.handle_connection)
        ack_line_idx = src.index("writer.write(b'\\x00')")
        following = src[ack_line_idx:ack_line_idx + 300]
        self.assertIn('asyncio.wait_for', following)
        self.assertIn('writer.drain()', following)
        self.assertIn('_HANDSHAKE_TIMEOUT_SEC', following)


if __name__ == '__main__':
    unittest.main()
