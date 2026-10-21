"""Tests for anetbbs/features/mud/__init__.py's launch_mud() entry
point: the Phase 2 (MUD telnet protocol extensions) hookup, character
creation on first play, and cleanup guarantees (presence removal +
protocol negotiate_stop always run, even on an exception).

Reached either via the Game Center path (GameManager._launch() calling
this in-process with a real BBSSession, over telnet/SSH/rlogin) or via
the subprocess+PTY bridge (door_runner.py/builtin_runner.py's
_StdioSession) -- which is what the WEB UI's own Game Center actually
uses for every door game including this one, as well as a direct
BbsMenuItem 'door' action. See docs/40-mud.md's own "a real limitation
worth knowing about" section: the subprocess path can't reach
session.mud_protocol_negotiate_start()/send_gmcp() at all (confirmed:
_StdioSession only exposes write/read_line/read_raw and a plain .user
dict), but ordinary play works identically either way -- see
tests/test_builtin_runner_stdio_session_user_id.py and
tests/test_door_runner_bbs_user_id_env.py for the real live bug this
used to have (always reported "not logged in" through that path,
including a genuinely logged-in web user) and its fix.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _FakeSession:
    """Shaped like a real BBSSession: has mud_protocol_negotiate_
    start()/_stop() (spied via AsyncMock) and a plain read_line()
    queue. One real BBSSession instance covers both telnet/SSH/rlogin
    and the web terminal -- this fake stands in for all of them."""

    def __init__(self, responses, user=None, with_protocol_methods=True):
        self.user = user
        self._responses = list(responses)
        self.written = []
        if with_protocol_methods:
            self.mud_protocol_negotiate_start = AsyncMock()
            self.mud_protocol_negotiate_stop = AsyncMock()

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._responses.pop(0) if self._responses else 'quit'

    def transcript(self):
        return ''.join(self.written)


class LaunchMudProtocolIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        u = User(username='mudplayer', email='mudplayer@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

    def test_requires_a_logged_in_user(self):
        from anetbbs.features.mud import launch_mud
        session = _FakeSession([], user=None)
        asyncio.run(launch_mud(session, 'nobody'))
        self.assertIn('need to be logged in', session.transcript())

    def test_negotiate_start_called_once_with_real_mssp_vars(self):
        from anetbbs.features.mud import launch_mud
        session = _FakeSession(['Hero', 'warrior', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        session.mud_protocol_negotiate_start.assert_awaited_once()
        _, kwargs = session.mud_protocol_negotiate_start.call_args
        mssp = kwargs['mssp_vars']
        self.assertEqual(mssp['NAME'], 'The Shattered Realm')
        self.assertIn('PLAYERS', mssp)
        self.assertIn('UPTIME', mssp)

    def test_negotiate_stop_called_on_normal_quit(self):
        from anetbbs.features.mud import launch_mud
        session = _FakeSession(['Hero', 'warrior', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        session.mud_protocol_negotiate_stop.assert_awaited_once()

    def test_negotiate_stop_still_called_if_the_main_loop_raises(self):
        """try/finally must run cleanup even on an unexpected error
        during actual play -- confirmed by letting character creation
        succeed normally (so mud_protocol_negotiate_start() actually
        runs) and then exploding on the next read_line() call (the
        main command-loop prompt)."""
        from anetbbs.features.mud import launch_mud

        class _ExplodingSession(_FakeSession):
            async def read_line(self, prompt=''):
                if prompt:
                    await self.write(prompt)
                if self._responses:
                    return self._responses.pop(0)
                raise RuntimeError('boom')

        session = _ExplodingSession(['Hero', 'warrior'],
                                    user={'id': self.user_id, 'username': 'mudplayer'})
        with self.assertRaises(RuntimeError):
            asyncio.run(launch_mud(session, 'mudplayer'))
        session.mud_protocol_negotiate_start.assert_awaited_once()
        session.mud_protocol_negotiate_stop.assert_awaited_once()

    def test_missing_protocol_methods_does_not_crash_the_game(self):
        """A session shaped like builtin_runner.py's own _StdioSession
        (no mud_protocol_negotiate_start/_stop at all) must still let
        the player actually play -- confirmed real: that shim only
        exposes write/read_line/read_raw and a plain .user dict."""
        from anetbbs.features.mud import launch_mud
        session = _FakeSession(['Hero', 'warrior', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'},
                               with_protocol_methods=False)
        asyncio.run(launch_mud(session, 'mudplayer'))  # must not raise
        self.assertIn('Welcome', session.transcript())

    def test_character_created_on_first_play(self):
        from anetbbs.features.mud import launch_mud
        from anetbbs.models import MudCharacter
        session = _FakeSession(['Hero', 'warrior', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        char = MudCharacter.query.filter_by(user_id=self.user_id).first()
        self.assertIsNotNone(char)
        self.assertEqual(char.name, 'Hero')
        self.assertEqual(char.char_class, 'warrior')
        self.assertIsNotNone(char.current_room_id)

    def test_character_creation_lists_every_class_and_applies_its_stats(self):
        from anetbbs.features.mud import launch_mud
        from anetbbs.models import MudCharacter
        session = _FakeSession(['Mage Hero', 'mage', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        txt = session.transcript()
        for label in ('Warrior', 'Mage', 'Rogue', 'Cleric'):
            self.assertIn(label, txt)
        char = MudCharacter.query.filter_by(user_id=self.user_id).first()
        self.assertEqual(char.char_class, 'mage')
        self.assertGreater(char.mana_max, 0)  # Mage gets a real mana pool

    def test_class_description_lines_never_exceed_79_columns(self):
        """Real bug reported live (screenshots): the class list was
        written as one unwrapped line per class and left to the
        terminal's own hardware auto-wrap, which breaks mid-word with
        no word-boundary awareness. See character.word_wrap()'s own
        docstring for the full writeup."""
        from anetbbs.features.mud import launch_mud
        session = _FakeSession(['Hero', 'warrior', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        for physical_line in session.transcript().split('\r\n'):
            self.assertLessEqual(len(physical_line), 79,
                                 msg=f'line exceeds 79 columns: {physical_line!r}')

    def test_invalid_class_choice_reprompts_then_defaults_to_warrior(self):
        from anetbbs.features.mud import launch_mud
        from anetbbs.models import MudCharacter
        session = _FakeSession(['Hero', 'nonsense', 'nonsense', 'nonsense', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        self.assertIn('Defaulting to Warrior', session.transcript())
        char = MudCharacter.query.filter_by(user_id=self.user_id).first()
        self.assertEqual(char.char_class, 'warrior')

    def test_existing_character_skips_creation_prompt(self):
        from anetbbs.features.mud import launch_mud
        from anetbbs.models import db, MudCharacter
        char = MudCharacter(user_id=self.user_id, name='AlreadyExists')
        db.session.add(char)
        db.session.commit()
        session = _FakeSession(['quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        self.assertNotIn("character's name", session.transcript())
        self.assertIn('AlreadyExists', session.transcript())

    def test_blank_name_reprompts_instead_of_crashing(self):
        from anetbbs.features.mud import launch_mud
        session = _FakeSession(['', '', 'Hero', 'rogue', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        from anetbbs.models import MudCharacter
        char = MudCharacter.query.filter_by(user_id=self.user_id).first()
        self.assertIsNotNone(char)
        self.assertEqual(char.name, 'Hero')
        self.assertEqual(char.char_class, 'rogue')

    def test_presence_removed_on_exit(self):
        from anetbbs.features.mud import launch_mud
        from anetbbs.features.mud import mp as mud_mp
        session = _FakeSession(['Hero', 'warrior', 'quit'],
                               user={'id': self.user_id, 'username': 'mudplayer'})
        asyncio.run(launch_mud(session, 'mudplayer'))
        self.assertNotIn('mudplayer', mud_mp.list_players())


if __name__ == '__main__':
    unittest.main()
