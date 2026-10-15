"""Regression test for a real gap found in a follow-up security audit:
play_rlogin_telnet()'s and play_telnet_terminal()'s WRITE-side
writer.drain() calls (the rlogin handshake, telnet IAC-negotiation
replies, and the input pumps that forward the user's own keystrokes to
the remote door) had NO timeout at all -- the sibling bug to the one
test_external_door_idle_timeout.py already covers on the READ side.

asyncio's drain() has no built-in timeout: a remote door server that
accepts the TCP connection and WRITES data (so the already-fixed
read-side idle timeout never fires) but stops READING from its own
socket -- without closing it -- leaves the BBS side's own drain() call
blocked forever once the OS send buffer fills, freezing the whole
bridged session exactly like the read-side bug did, just triggered by
the opposite direction going quiet.

Uses a fake asyncio.open_connection() (patched directly) rather than a
real TCP server, since reliably forcing a real OS send-buffer-full
condition in a fast unit test is not practical -- a writer whose
drain() simply never completes is the same real-world condition
(drain() blocks until the transport's write buffer drains below its
low-water mark) without depending on OS buffer sizing.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _NeverEndingReader:
    """Remote never sends anything further -- keeps the output pump
    alive on its own idle timeout (not under test here) so the ONLY
    thing that can end the bridge quickly is the write-side fix."""
    async def read(self, n=4096):
        await asyncio.sleep(3600)


class _HangingDrainWriter:
    """write() succeeds immediately (as a real socket would, buffering
    locally); drain() never completes -- simulates a remote that
    stopped reading without closing the socket."""
    def __init__(self):
        self.written = bytearray()
        self._closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        await asyncio.sleep(3600)

    def close(self):
        self._closed = True

    async def wait_closed(self):
        pass

    def get_extra_info(self, key, default=None):
        return default


class _OneKeyThenBlockingReader:
    """Yields exactly one keystroke byte, then blocks forever -- for
    driving the telnet bridge's input pump exactly once."""
    def __init__(self, byte=b'x'):
        self._byte = byte
        self._sent = False
        self._block = asyncio.Event()

    async def read(self, n=1):
        if not self._sent:
            self._sent = True
            return self._byte
        await self._block.wait()
        return b''


class _FakeSession:
    def __init__(self, reader=None):
        self.written = []
        self.reader = reader if reader is not None else _NeverEndingReader()
        self.read_line_calls = []
        self.encoding = 'cp437'

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        self.read_line_calls.append(prompt)
        return ''


class ExternalDoorWriteSideDrainTimeoutTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'write_drain_timeout.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User(username='doorplayer2', email='doorplayer3@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            self.user = {'id': u.id, 'username': 'doorplayer2',
                        'display_name': 'Door Player 2'}

        from anetbbs.games import node_manager
        node_manager._active.clear()

    def _make_rlogin_game(self):
        from anetbbs.models import db, Game
        g = Game(name='Test Rlogin Drain Door', slug='test-rlogin-drain',
                game_type='door_rlogin', executable_path='127.0.0.1:1',
                command_line_args='@USER@ testpass', max_nodes=1)
        db.session.add(g)
        db.session.commit()
        return g

    def _make_telnet_game(self):
        from anetbbs.models import db, Game
        g = Game(name='Test Telnet Drain Door', slug='test-telnet-drain',
                game_type='door_telnet', executable_path='127.0.0.1:1',
                max_nodes=1)
        db.session.add(g)
        db.session.commit()
        return g

    def test_rlogin_handshake_drain_does_not_hang_forever(self):
        """The handshake write (the very first thing play_rlogin_telnet
        does after connecting) used to have a raw, unprotected
        drain()."""
        from anetbbs.games.door_runner import play_rlogin_telnet

        session = _FakeSession()
        fake_writer = _HangingDrainWriter()

        async def _fake_open_connection(host, port):
            return _NeverEndingReader(), fake_writer

        async def _drive():
            game = self._make_rlogin_game()
            with patch.dict(os.environ, {'DOOR_IDLE_TIMEOUT': '1'}), \
                 patch('asyncio.open_connection',
                       _fake_open_connection):
                return await asyncio.wait_for(
                    play_rlogin_telnet(game, self.user, session), timeout=5)

        with self.app.app_context():
            result = asyncio.run(_drive())

        # The handshake drain times out -> the function's own except
        # branch runs -> returns False (connect/handshake failure),
        # rather than hanging forever.
        self.assertFalse(result)

    def test_telnet_input_pump_drain_does_not_hang_forever(self):
        """A keystroke forwarded to the remote door used to have a raw,
        unprotected drain() in the input pump."""
        from anetbbs.games.door_runner import play_telnet_terminal

        fake_reader_keystrokes = _OneKeyThenBlockingReader()
        session = _FakeSession(reader=fake_reader_keystrokes)
        fake_writer = _HangingDrainWriter()

        async def _fake_open_connection(host, port):
            return _NeverEndingReader(), fake_writer

        async def _drive():
            game = self._make_telnet_game()
            with patch.dict(os.environ, {'DOOR_IDLE_TIMEOUT': '1'}), \
                 patch('asyncio.open_connection',
                       _fake_open_connection):
                return await asyncio.wait_for(
                    play_telnet_terminal(game, self.user, session), timeout=5)

        with self.app.app_context():
            result = asyncio.run(_drive())

        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
