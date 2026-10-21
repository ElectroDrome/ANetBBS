"""Regression test for a real live bug: playing The Shattered Realm (or
any other builtin_python game that needs a real numeric user id, not
just a username string) from the web UI always failed with "You need
to be logged in to play" -- even for a genuinely logged-in user.

Root cause: anetbbs/web/games.py's web '/play' route for a
builtin_python game always launches it through
anetbbs/games/door_runner.py's launch_door_game() -> builtin_runner.py
subprocess+PTY bridge (the same machinery every other door game type
uses), NOT through the in-process anetbbs.features.games.GameManager
path a real BBSSession gets over telnet/SSH/rlogin. builtin_runner.py's
_StdioSession (the session shim handed to the game's own entry point
inside that subprocess) only ever carried 'username'/'display_name'/
'access_level' in its .user dict -- no 'id' at all. anetbbs/features/
mud/__init__.py's launch_mud() needs session.user['id'] to look up/
create the player's MudCharacter row (a real user_id FK, unlike
ANetCRAFT's own username-keyed state, which never needed this and so
never surfaced the gap). With no 'id' ever present, launch_mud() always
concluded the player wasn't logged in, regardless of whether they
actually were.

Fixed at the shared chokepoint both bugs flow through: door_runner.py
now also exports BBS_USER_ID into the child's environment (alongside
the existing BBS_USERNAME/BBS_REAL_NAME/etc., using the same _ufield()
helper that already handles both a dict-shaped user (telnet/SSH) and a
real User model (web's current_user)), and _StdioSession reads it back
into self.user['id']. This also fixes the identical gap for a game
wired in via a direct BbsMenuItem 'door' action (menu_engine.py's
separate dispatch, which uses this exact same subprocess path for
every game type including builtin_python) -- not just the web UI case
this was first reported from.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.games.builtin_runner import _StdioSession


class StdioSessionUserIdTests(unittest.TestCase):
    def test_user_id_populated_from_env_var(self):
        with patch.dict(os.environ, {'BBS_USER_ID': '42', 'BBS_USERNAME': 'stingray'}):
            session = _StdioSession(reader=None)
        self.assertEqual(session.user['id'], 42)

    def test_user_id_is_none_when_env_var_missing(self):
        env = dict(os.environ)
        env.pop('BBS_USER_ID', None)
        with patch.dict(os.environ, env, clear=True):
            session = _StdioSession(reader=None)
        self.assertIsNone(session.user['id'])

    def test_user_id_is_none_when_env_var_is_zero(self):
        """0 is never a real row id (see door_runner.py's own _ufield()
        default-to-0 convention) -- treated the same as missing, not a
        real id a MudCharacter lookup should ever match against."""
        with patch.dict(os.environ, {'BBS_USER_ID': '0'}):
            session = _StdioSession(reader=None)
        self.assertIsNone(session.user['id'])

    def test_user_id_is_none_when_env_var_is_garbage(self):
        """Defensive only -- a malformed value must not crash game
        launch, just fall back to 'not logged in' like a real guest."""
        with patch.dict(os.environ, {'BBS_USER_ID': 'not-a-number'}):
            session = _StdioSession(reader=None)
        self.assertIsNone(session.user['id'])

    def test_username_and_display_name_still_work_unchanged(self):
        with patch.dict(os.environ, {'BBS_USER_ID': '7', 'BBS_USERNAME': 'stingray',
                                     'BBS_REAL_NAME': 'Captain StingRay'}):
            session = _StdioSession(reader=None)
        self.assertEqual(session.user['username'], 'stingray')
        self.assertEqual(session.user['display_name'], 'Captain StingRay')


class _FakeReader:
    """Stands in for the asyncio.StreamReader _StdioSession.read_line()
    reads from -- feeds back one scripted byte per .read(1) call."""
    def __init__(self, data: bytes):
        self._chunks = [bytes([b]) for b in data]

    async def read(self, n=1):
        return self._chunks.pop(0) if self._chunks else b''


class ReadLineEchoTests(unittest.TestCase):
    """Real bug reported live (screenshots): typing a character name or
    class choice at the web UI's Game Center showed NOTHING on screen
    at all. door_runner.py puts this subprocess's PTY slave in raw mode
    (tty.setraw(0) in builtin_runner.py's own main()) specifically so
    single keypresses reach the game without waiting for Enter -- but
    raw mode also disables the terminal's own local echo entirely, and
    read_line() never echoed anything itself either (only backspace had
    an erase-in-place sequence). Fixed to mirror real BBSSession.
    read_line()'s own echo convention exactly (anetbbs/core/session.py):
    echo each printable character as it's read, echo '\\r\\n' on Enter,
    same backspace erase sequence as before."""

    def _session(self, input_bytes):
        session = _StdioSession(reader=_FakeReader(input_bytes))
        session.write = AsyncMock()
        return session

    def test_printable_characters_are_echoed_as_typed(self):
        session = self._session(b'Hi\r')
        result = asyncio.run(session.read_line())
        self.assertEqual(result, 'Hi')
        echoed = [c.args[0] for c in session.write.call_args_list]
        self.assertIn('H', echoed)
        self.assertIn('i', echoed)

    def test_enter_echoes_crlf(self):
        session = self._session(b'X\r')
        asyncio.run(session.read_line())
        echoed = [c.args[0] for c in session.write.call_args_list]
        self.assertIn('\r\n', echoed)

    def test_backspace_still_erases_in_place(self):
        session = self._session(b'Ab\x7f\r')
        result = asyncio.run(session.read_line())
        self.assertEqual(result, 'A')
        echoed = [c.args[0] for c in session.write.call_args_list]
        self.assertIn('\x08 \x08', echoed)

    def test_prompt_is_written_before_any_echo(self):
        session = self._session(b'\r')
        asyncio.run(session.read_line(prompt='Name: '))
        first_call = session.write.call_args_list[0].args[0]
        self.assertEqual(first_call, 'Name: ')


class RunPushesAppContextTests(unittest.TestCase):
    """Second real live bug in the same launch path, found right after
    the BBS_USER_ID fix above shipped: fixing the user-id gap let
    launch_mud() get far enough to actually query the database
    (MudCharacter.query.filter_by(user_id=...)) -- and this subprocess
    (spawned fresh via fork+exec from door_runner.py, with no Flask app
    of its own) had never pushed a Flask app context at all. Every
    other builtin_python game (ANetCRAFT) never touches the DB directly
    so this never surfaced before. Confirmed live: "RuntimeError:
    Working outside of application context" the moment launch_mud()
    reached its first MudCharacter query, immediately after the
    previous fix made it past the login check.

    Fixed by building a throwaway Flask app in main() (same
    Flask()+get_config()+db.init_app() shape door_runner.py's own
    _cleanup_session_safe()/GameManager._launch() already use for
    identical reasons) and pushing it via the shared
    transient_app_context() helper for the whole interactive game
    session, not just one query.
    """

    def setUp(self):
        import tempfile
        import anetbbs.config as cfg_mod
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = str(Path(self._tmp.name) / 'a.db')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        self.app = create_app('testing')
        self.app.config['TESTING'] = True
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def test_run_pushes_app_context_so_db_access_works(self):
        import asyncio
        from anetbbs.games.builtin_runner import _run

        async def fake_game(session, username):
            from anetbbs.models import User
            User.query.first()  # must not raise

        asyncio.run(_run(fake_game, 'tester', self.app))  # must not raise

    def test_without_the_wrapper_db_access_raises(self):
        """Calling _play() directly (the actual game-running code, minus
        _run()'s app-context wrapper) reproduces the real live bug --
        proves the wrapper is the thing actually preventing it, not a
        redundant safety net."""
        import asyncio
        from anetbbs.games.builtin_runner import _play

        async def fake_game(session, username):
            from anetbbs.models import User
            User.query.first()

        with self.assertRaises(RuntimeError):
            asyncio.run(_play(fake_game, 'tester'))


class RealScriptEntryPointTests(unittest.TestCase):
    """Real bug found live, right after the app-context fix above
    shipped: that fix's `from ..features.db_scope import
    transient_app_context` is a RELATIVE import, which only resolves
    when builtin_runner.py is imported as part of the anetbbs.games
    package -- exactly what every test above does, and exactly NOT how
    production actually runs this file. door_runner.py's own
    _build_builtin_python_command() (anetbbs/games/door_runner.py)
    invokes it as `[sys.executable, <path-to-builtin_runner.py>,
    <module:function>]` -- a plain top-level script, where __name__ ==
    '__main__' and __package__ is empty, so the relative import raised
    "ImportError: attempted relative import with no known parent
    package" the instant a player actually tried to play. Fixed by
    using the same absolute `anetbbs.features.db_scope` import already
    used for every other anetbbs.* import in this file. This test
    drives the REAL script entry point as a subprocess -- the only way
    this class of bug (import-resolution differs by how a module was
    reached) can be caught at all; an imported-function-level test can
    never see it, which is exactly how the bug shipped past review
    once already.
    """

    def test_real_script_invocation_does_not_hit_relative_import_error(self):
        import subprocess
        import sys
        import tempfile
        import textwrap

        runner = str(Path(__file__).resolve().parents[1] /
                    'anetbbs' / 'games' / 'builtin_runner.py')

        with tempfile.TemporaryDirectory() as tmp:
            mod_path = Path(tmp) / 'fake_builtin_runner_game_mod.py'
            mod_path.write_text(textwrap.dedent('''
                async def launch(session, username):
                    await session.write('ready\\n')
            '''))
            env = dict(os.environ)
            env['PYTHONPATH'] = tmp + os.pathsep + env.get('PYTHONPATH', '')
            env['FLASK_ENV'] = 'testing'
            env['BBS_USERNAME'] = 'tester'
            proc = subprocess.run(
                [sys.executable, runner, 'fake_builtin_runner_game_mod:launch'],
                input=b'', capture_output=True, env=env, timeout=20)

        stderr = proc.stderr.decode(errors='replace')
        self.assertNotIn('ImportError', stderr, msg=f'stderr: {stderr}')
        self.assertNotIn('relative import', stderr, msg=f'stderr: {stderr}')
        self.assertEqual(proc.returncode, 0, msg=f'stderr: {stderr}')


if __name__ == '__main__':
    unittest.main()
