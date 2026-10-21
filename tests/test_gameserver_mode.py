"""Regression tests for the standalone game-server deployment mode
(Phase 1 of the door-games roadmap, 2026-10-20).

Jerry runs a SEPARATE install of Synchronet today configured purely as
a door server: rlogin-only, straight to the game/door menu, no BBS
menus at all. GAMESERVER_MODE_ENABLED lets an ANetBBS install do the
same, for both rlogin and SSH (both funnel into the identical
BBSSession.start() post-login dispatch -- confirmed directly:
anetbbs/core/ssh_server.py's _handle() constructs a BBSSession the
same way anetbbs/core/rlogin_server.py does and calls the same
.start(), so one new branch in start() covers both protocols with no
SSH-specific code needed).

Reused precedent: anetbbs/core/session.py's own `_direct_door_slug`
feature (the per-CONNECTION opt-in this site-wide mode builds on top
of) is tested the same two ways this file uses -- direct unit tests for
the resolvable pieces, and AST-structural tests for the branch inside
start() itself, since start() is a large, monolithic async method with
real network/DB I/O threaded all the way through (not practically
drivable end-to-end in a unit test just to reach one branch -- see
test_rlogin_direct_door_launch.py's own docstring for the same
reasoning, applied here to a sibling branch in the identical method).
"""
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWriter:
    def get_extra_info(self, key):
        return None


class ReadEnvFileHelperTests(unittest.TestCase):
    """BBSSession._read_env_file_value()/_read_env_file_bool() -- the
    shared live-without-restart .env reader GAMESERVER_MODE_ENABLED/
    GAMESERVER_DEFAULT_DOOR_SLUG use (also now used by the
    pre-existing FAST_LOGON_ENABLED, refactored to share this instead
    of its own separate inline copy)."""

    def _session_with_env_file(self, contents):
        from anetbbs.core.session import BBSSession
        session = BBSSession(reader=None, writer=_FakeWriter(), config={})
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp_dir, ignore_errors=True))
        env_path = Path(tmp_dir) / '.env'
        env_path.write_text(contents)
        # _read_env_file_value resolves the path relative to this
        # module's own location (two dirs up from core/session.py) --
        # patch it to point at our throwaway file instead of the real
        # repo .env for this test.
        session._read_env_file_value = (
            lambda key, default='', _path=str(env_path): self._read_from(_path, key, default))
        return session

    @staticmethod
    def _read_from(path, key, default):
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith(key + '='):
                        return line.split('=', 1)[1].strip()
        except Exception:
            pass
        return default

    def test_reads_an_existing_key(self):
        session = self._session_with_env_file('GAMESERVER_MODE_ENABLED=true\n')
        self.assertEqual(session._read_env_file_value('GAMESERVER_MODE_ENABLED'), 'true')

    def test_missing_key_returns_default(self):
        session = self._session_with_env_file('SOME_OTHER_KEY=x\n')
        self.assertEqual(session._read_env_file_value('GAMESERVER_MODE_ENABLED'), '')
        self.assertEqual(
            session._read_env_file_value('GAMESERVER_MODE_ENABLED', 'fallback'), 'fallback')

    def test_bool_helper_accepts_true_1_yes(self):
        for val in ('true', '1', 'yes', 'TRUE', 'Yes'):
            session = self._session_with_env_file(f'GAMESERVER_MODE_ENABLED={val}\n')
            self.assertTrue(session._read_env_file_bool('GAMESERVER_MODE_ENABLED'),
                            msg=f'{val!r} should be truthy')

    def test_bool_helper_false_for_anything_else(self):
        for val in ('false', '0', 'no', '', 'nonsense'):
            session = self._session_with_env_file(f'GAMESERVER_MODE_ENABLED={val}\n')
            self.assertFalse(session._read_env_file_bool('GAMESERVER_MODE_ENABLED'),
                             msg=f'{val!r} should be falsy')

    def test_real_unpatched_reader_handles_an_unknown_key_gracefully(self):
        """Exercises the REAL (unpatched) method directly against
        whatever .env this sandbox actually has -- a key that
        definitely isn't in it must return the given default rather
        than raising, confirming the except/pass + default-return path
        works end to end (not just the test's own patched stand-in
        above)."""
        from anetbbs.core.session import BBSSession
        session = BBSSession(reader=None, writer=_FakeWriter(), config={})
        result = session._read_env_file_value('THIS_KEY_DEFINITELY_DOES_NOT_EXIST_XYZ', 'default')
        self.assertEqual(result, 'default')


class GameserverModeStartBranchStructureTests(unittest.TestCase):
    """start() is a large, monolithic async method with real network/DB
    I/O threaded all the way through -- verified structurally (AST),
    same approach test_rlogin_direct_door_launch.py's own
    StartDirectDoorBranchStructureTests already established for the
    sibling _direct_door_slug branch in this identical method."""

    def test_branch_checks_gameserver_mode_enabled(self):
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        self.assertIn("self._read_env_file_bool('GAMESERVER_MODE_ENABLED')", source)

    def test_branch_runs_after_the_direct_door_slug_branch(self):
        """A per-connection xtrn=<slug> request must win over the
        site-wide GAMESERVER_MODE_ENABLED default -- checked by
        confirming the direct_door_slug branch's own source appears
        BEFORE the gameserver-mode branch's in start()'s source."""
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        direct_door_idx = source.index('if self._direct_door_slug:')
        gameserver_idx = source.index("self._read_env_file_bool('GAMESERVER_MODE_ENABLED')")
        self.assertLess(
            direct_door_idx, gameserver_idx,
            'the per-connection direct-door-slug branch must be checked '
            'before the site-wide GAMESERVER_MODE_ENABLED default')

    def test_branch_falls_back_to_the_door_menu_not_a_bare_return(self):
        """Whether or not a GAMESERVER_DEFAULT_DOOR_SLUG is configured
        (or launches successfully), the branch must end by showing the
        door menu and returning -- never falling through to logon
        modules or the normal BBS main menu, which is the entire point
        of this being a GAME server, not a BBS."""
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        gameserver_idx = source.index("self._read_env_file_bool('GAMESERVER_MODE_ENABLED')")
        # Slice from the branch to the next top-level comment block
        # (the Fast logon section) to scope the check to just this
        # branch's own body.
        fast_logon_idx = source.index('# Fast logon check', gameserver_idx)
        branch_src = source[gameserver_idx:fast_logon_idx]
        self.assertIn('self.games.show_door_menu()', branch_src)
        self.assertIn('return', branch_src)

    def test_branch_runs_after_presence_is_established(self):
        """Same requirement as the direct-door-slug branch (games.py's
        _launch() / show_door_menu() calls presence.set_page(...))."""
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        presence_idx = source.index('self.presence = presence')
        gameserver_idx = source.index("self._read_env_file_bool('GAMESERVER_MODE_ENABLED')")
        self.assertGreater(gameserver_idx, presence_idx)


class SshFunnelsIntoTheSameStartMethodTests(unittest.TestCase):
    """Confirms the architectural claim the whole feature rests on:
    SSH logins reach the exact same BBSSession.start() as rlogin/
    telnet, so one new branch there covers SSH for free with no
    SSH-specific code."""

    def test_ssh_server_calls_the_same_bbssession_start(self):
        import anetbbs.core.ssh_server as ssh_mod
        source = inspect.getsource(ssh_mod)
        self.assertIn('BBSSession(', source)
        self.assertIn('session.start()', source)


if __name__ == '__main__':
    unittest.main()
