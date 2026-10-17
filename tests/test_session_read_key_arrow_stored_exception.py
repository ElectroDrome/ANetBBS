"""Regression test for a real gap found in a follow-up audit:
BBSSession.read_key_arrow()'s CSI/SS3 continuation-byte reads (the
bytes following a leading ESC) caught `except (asyncio.TimeoutError,
Exception): pass`/`return 'ESC'` WITHOUT first checking whether the
reader already has a real stored connection exception on it (see
_reader_stored_exception's own docstring for the confirmed mechanism:
a replayed stored exception surfaces as this exact TimeoutError type,
indistinguishable from a genuine idle wait by type alone). Every other
read path in this file (read_raw(), _afk_peek(),
_read_byte_maybe_spinning()) already makes this check -- the three
continuation-byte reads inside read_key_arrow() did not, so a
connection dying in the narrow window between the leading ESC byte and
its continuation byte(s) produced a single swallowed/phantom 'ESC'
return instead of the CarrierLost every other disconnect path raises.

Not a busy-loop/freeze by itself (the function returns once per call,
it doesn't spin) -- cosmetic/correctness only: the very next read on
that same dead reader correctly raises CarrierLost via
_read_byte_maybe_spinning()'s own already-checked path. Still worth
closing for consistency with the rest of the file's own documented
discipline.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession, CarrierLost


class _ScriptedReader:
    """Returns bytes from `script` in order. Once the script is
    exhausted: raises `tail_exc` immediately (simulating a stored
    connection exception being replayed, exactly like real
    asyncio.StreamReader.read() does) if set, otherwise sleeps
    `tail_sleep` seconds before returning EOF (simulating a genuine,
    not-yet-arrived continuation byte)."""

    def __init__(self, script, tail_exc=None, tail_sleep=None):
        self._script = list(script)
        self._tail_exc = tail_exc
        self._tail_sleep = tail_sleep

    def exception(self):
        return self._tail_exc

    async def read(self, n=1):
        if self._script:
            return self._script.pop(0)
        if self._tail_exc is not None:
            raise self._tail_exc
        if self._tail_sleep is not None:
            await asyncio.sleep(self._tail_sleep)
        return b''


class _FakeWriter:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def get_extra_info(self, key, default=None):
        return ('1.2.3.4', 1234) if key == 'peername' else default

    def is_closing(self):
        return False

    def close(self):
        pass


def _make_session(reader):
    session = BBSSession(reader, _FakeWriter(), config={})
    session.window_size = (80, 24)
    return session


class ReadKeyArrowStoredExceptionTests(unittest.TestCase):
    def test_dead_connection_right_after_leading_esc_raises_carrierlost(self):
        exc = TimeoutError('ETIMEDOUT')  # a real stored OS-level timeout
        reader = _ScriptedReader([b'\x1b'], tail_exc=exc)
        session = _make_session(reader)

        with self.assertRaises(CarrierLost):
            asyncio.run(asyncio.wait_for(session.read_key_arrow(), timeout=5))

    def test_dead_connection_mid_csi_sequence_raises_carrierlost(self):
        exc = TimeoutError('ETIMEDOUT')
        reader = _ScriptedReader([b'\x1b', b'['], tail_exc=exc)
        session = _make_session(reader)

        with self.assertRaises(CarrierLost):
            asyncio.run(asyncio.wait_for(session.read_key_arrow(), timeout=5))

    def test_dead_connection_mid_ss3_sequence_raises_carrierlost(self):
        exc = TimeoutError('ETIMEDOUT')
        reader = _ScriptedReader([b'\x1b', b'O'], tail_exc=exc)
        session = _make_session(reader)

        with self.assertRaises(CarrierLost):
            asyncio.run(asyncio.wait_for(session.read_key_arrow(), timeout=5))

    def test_genuine_idle_wait_after_plain_esc_still_returns_esc(self):
        """A real user who just pressed the plain ESC key with no
        continuation bytes coming -- no stored exception, just a
        legitimate per-byte read timing out. Confirms the fix doesn't
        misclassify this normal case as a dead connection."""
        reader = _ScriptedReader([b'\x1b'], tail_exc=None, tail_sleep=1.0)
        session = _make_session(reader)

        result = asyncio.run(asyncio.wait_for(session.read_key_arrow(), timeout=5))
        self.assertEqual(result, 'ESC')

    def test_a_complete_arrow_sequence_with_no_gaps_still_works(self):
        reader = _ScriptedReader([b'\x1b', b'[', b'A'])
        session = _make_session(reader)

        result = asyncio.run(asyncio.wait_for(session.read_key_arrow(), timeout=5))
        self.assertEqual(result, 'UP')


if __name__ == '__main__':
    unittest.main()
