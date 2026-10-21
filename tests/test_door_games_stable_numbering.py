"""Regression tests for the Game Center / Doors admin UX fix (Phase 0
of the door-games roadmap, 2026-10-20).

Real bug reported live: the number a player types to pick a door in the
terminal menu (anetbbs/features/games.py's GameManager.show_door_menu()/
_show_category_submenu()) used to be a live `enumerate()` rank rebuilt
every redraw, not a stored value -- so adding, removing, or reordering
ANY door anywhere shifted every later door's displayed number, silently
invalidating a sysop's own hand-drawn ANSI door-menu art that hardcodes
option numbers ("I have had to remove my ansi menus multiple times
because the numbers change"). Fixed by displaying/resolving each game's
own Game.sort_order directly (or, for an as_submenu=True category's
stand-in entry, that category's own GameCategory.sort_order) instead of
a recomputed rank -- a game's own number now only changes when ITS OWN
sort_order changes.

Also covers the new admin-side tooling that goes with the fix:
bulk drag-and-drop reorder (/admin/games/reorder), one-click clean
renumber (/admin/games/renumber, /admin/games/categories/renumber), and
the advisory sort_order-collision warning on save.

Uses a real seeded-from-scratch DB (not full _create_default_data(),
which this file doesn't need) -- same create_app()/db.create_all()
pattern as sibling admin-route tests (see
test_admin_afk_warning_setting.py) for the admin-route tests, and the
same _FakeSession pattern as test_door_games_menu_layout.py for the
terminal-menu tests (duplicated here rather than imported, matching
this project's established per-file-fixture convention).
"""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class _FakeWriter:
    def __init__(self, sink):
        self._sink = sink

    def write(self, data):
        self._sink.append(data.decode('latin-1', errors='replace'))

    async def drain(self):
        pass


class _FakeSession:
    def __init__(self, responses, width=80):
        self.user = None
        self._responses = list(responses)
        self.written = []
        self.window_size = (width, 24)
        self.term_mode = 'ansi'
        self.writer = _FakeWriter(self.written)

    async def write(self, text):
        self.written.append(text)

    async def _drain_protected(self):
        await self.writer.drain()

    async def clear_screen(self):
        await self.write('\x1b[2J\x1b[H\x1b[0m')

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    def transcript(self):
        return ''.join(self.written)


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class StableNumberingTerminalMenuTests(unittest.TestCase):
    """The core regression: a game's displayed/typed number is its own
    sort_order, and survives other list changes."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def _seed(self, *rows):
        from anetbbs.models import db, Game
        for name, slug, sort_order in rows:
            db.session.add(Game(
                name=name, slug=slug, category='other',
                game_type='door_native', is_active=True,
                sort_order=sort_order))
        db.session.commit()

    def test_displayed_number_is_sort_order_not_a_rank(self):
        self._seed(('First Door', 'first-door', 10),
                   ('Second Door', 'second-door', 20))
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = _strip_ansi(session.transcript())
        self.assertRegex(txt, r'\b10\b\.\s+First Door')
        self.assertRegex(txt, r'\b20\b\.\s+Second Door')

    def test_unrelated_games_number_survives_adding_a_new_game(self):
        """The actual reported bug: confirm Second Door's displayed
        number (its own sort_order, 20) is IDENTICAL before and after a
        brand new game is inserted earlier in the list -- the old
        enumerate()-rank code would have shifted it."""
        from anetbbs.models import db, Game
        self._seed(('Second Door', 'second-door', 20))
        from anetbbs.features.games import GameManager

        def _typed_number_for(label):
            probe = _FakeSession(['Q'])
            asyncio.run(GameManager(probe).show_door_menu())
            txt = _strip_ansi(probe.transcript())
            m = re.search(r'(\d+)\.\s+' + re.escape(label), txt)
            return m.group(1) if m else None

        before_number = _typed_number_for('Second Door')
        self.assertEqual(before_number, '20')

        # Insert a new game earlier in sort_order -- under the OLD
        # enumerate()-rank bug this would have bumped Second Door from
        # displayed "1" to displayed "2" (or similar); under the fix,
        # Second Door's own sort_order (20) is untouched.
        db.session.add(Game(
            name='Inserted First', slug='inserted-first', category='other',
            game_type='door_native', is_active=True, sort_order=5))
        db.session.commit()

        after_number = _typed_number_for('Second Door')
        self.assertEqual(
            after_number, '20',
            msg='Second Door\'s displayed number changed after an unrelated '
                'game was added -- this is exactly the reported bug')

    def test_typing_the_sort_order_number_launches_the_right_game(self):
        """Confirms resolution (not just display) uses sort_order --
        picking "20" must launch Second Door specifically, checked
        indirectly via _launch()'s failure path (a builtin_python game
        with no configured module logs/writes that game's own name)."""
        from anetbbs.models import db, Game
        self._seed(('First Door', 'first-door', 10))
        db.session.add(Game(
            name='Second Door', slug='second-door', category='other',
            game_type='builtin_python', is_active=True, sort_order=20,
            web_game_module=''))
        db.session.commit()
        from anetbbs.features.games import GameManager
        session = _FakeSession(['20', 'Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = session.transcript()
        self.assertIn('Game has no module configured', txt)

    def test_submenu_category_number_is_its_own_sort_order(self):
        from anetbbs.models import db, Game, GameCategory
        cat = GameCategory(name='Strategy Section', slug='stratsec',
                           as_submenu=True, sort_order=42)
        db.session.add(cat)
        db.session.add(Game(
            name='Inner Game', slug='inner-game', category='stratsec',
            game_type='door_native', is_active=True, sort_order=1))
        db.session.commit()
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = _strip_ansi(session.transcript())
        self.assertRegex(txt, r'\b42\b\.\s+Strategy Section')

    def test_submenu_category_screen_position_follows_its_own_sort_order(self):
        """Real bug reported live right after category drag-and-drop
        reordering shipped: dragging a category to a new position wrote
        a new GameCategory.sort_order (changing its displayed NUMBER)
        but its actual screen POSITION never moved, because the render
        walk used to plant a category's stand-in line wherever its
        FIRST MEMBER GAME fell in the flat Game.sort_order walk --
        completely independent of GameCategory.sort_order. Here, Late
        Category's own sort_order (9500) is HIGHER than Early
        Category's (9100), but Late Category's one member game has a
        much LOWER sort_order (9005) than Early Category's (9900) --
        under the old bug, Late Category would still render FIRST
        (following its member game), even though its own number (9500)
        says it belongs after Early Category (9100). The fix must make
        screen position follow each category's own sort_order, matching
        what a sysop actually sees/types."""
        from anetbbs.models import db, Game, GameCategory
        early_cat = GameCategory(name='Early Category', slug='early-cat-pos',
                                 as_submenu=True, sort_order=9100)
        late_cat = GameCategory(name='Late Category', slug='late-cat-pos',
                                as_submenu=True, sort_order=9500)
        db.session.add_all([early_cat, late_cat])
        db.session.add_all([
            Game(name='Early Member', slug='early-member-pos',
                category='early-cat-pos', game_type='door_native',
                is_active=True, sort_order=9900),
            Game(name='Late Member', slug='late-member-pos',
                category='late-cat-pos', game_type='door_native',
                is_active=True, sort_order=9005),
        ])
        db.session.commit()
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = _strip_ansi(session.transcript())
        early_pos = txt.index('Early Category')
        late_pos = txt.index('Late Category')
        self.assertLess(early_pos, late_pos,
                        msg='Early Category (sort_order 9100) must render '
                            'above Late Category (sort_order 9500) -- '
                            'screen position must follow each category\'s '
                            'OWN sort_order, not its member games\' numbers')

    def test_collision_first_claimant_wins_no_crash(self):
        """Two active games sharing a sort_order must not crash the
        menu -- the first one (by the existing (sort_order, name)
        query order) claims the number, matching the documented
        first-claimant-wins convention. Just renders + quits (no
        launch attempt) -- not what this test is checking."""
        self._seed(('Alpha', 'alpha-game', 99), ('Beta', 'beta-game', 99))
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        # Must not raise -- reaching here at all is the main assertion.
        self.assertIn('Alpha', session.transcript())
        self.assertIn('Beta', session.transcript())


class SubmenuPageStableNumberingTests(unittest.TestCase):
    """Same fix, applied to _show_category_submenu()'s own page
    listing -- the identical bug shape existed there too (enumerate()
    with a page offset)."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def test_picking_by_sort_order_in_a_submenu_launches_the_right_game(self):
        """create_app('testing') seeds the real bundled-door default
        data (ANetCRAFT, LORD, TW2002, ...) -- use deliberately
        out-of-range sort_order values (9000s) for this test's own rows
        so they can't collide with any real bundled default."""
        from anetbbs.models import db, Game, GameCategory
        cat = GameCategory(name='Arcade Section', slug='arcadesec',
                           as_submenu=True, sort_order=9000)
        db.session.add(cat)
        db.session.add_all([
            Game(name='Low Number', slug='low-number', category='arcadesec',
                game_type='door_native', is_active=True, sort_order=9005),
            Game(name='High Number', slug='high-number', category='arcadesec',
                game_type='builtin_python', is_active=True, sort_order=9077,
                web_game_module=''),
        ])
        db.session.commit()
        from anetbbs.features.games import GameManager
        # Enter the submenu (section's own number is 9000), then pick
        # "9077" (High Number's own sort_order, not its 1-based position
        # in the page, which would be "2").
        session = _FakeSession(['9000', '9077', 'B', 'Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = session.transcript()
        self.assertIn('Game has no module configured', txt)


class GamesAdminReorderRenumberTests(unittest.TestCase):
    """Admin-side tooling: bulk reorder endpoint, renumber endpoints,
    and the advisory sort_order-collision warning."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.games_reorder_admin_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='gamesreorderadmin',
                        email='gamesreorderadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_reorder_writes_gapped_sort_order_in_posted_order(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            a = Game(name='A', slug='reorder-a', category='other',
                    game_type='door_native', is_active=True, sort_order=1)
            b = Game(name='B', slug='reorder-b', category='other',
                    game_type='door_native', is_active=True, sort_order=2)
            db.session.add_all([a, b])
            db.session.commit()
            a_id, b_id = a.id, b.id

        client = self._client()
        resp = client.post('/admin/games/reorder', json={'order': [b_id, a_id]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

        with self.app.app_context():
            a2 = Game.query.get(a_id)
            b2 = Game.query.get(b_id)
            self.assertLess(b2.sort_order, a2.sort_order,
                            msg='B was posted first in the new order, so it '
                                'must end up with the lower sort_order')

    def test_reorder_categories_writes_clean_sort_order_in_posted_order(self):
        """as_submenu=True AND at least one visible active game required
        here -- only a submenu category that actually shows a numbered
        stand-in line at all participates in this reorder (see
        reorder_categories()'s own docstring)."""
        from anetbbs.models import db, Game, GameCategory
        with self.app.app_context():
            c1 = GameCategory(name='Reorder Cat One', slug='reorder-cat-one',
                              as_submenu=True, sort_order=1)
            c2 = GameCategory(name='Reorder Cat Two', slug='reorder-cat-two',
                              as_submenu=True, sort_order=2)
            db.session.add_all([c1, c2])
            db.session.add_all([
                Game(name='Reorder Cat One Game', slug='reorder-cat-one-game',
                    category='reorder-cat-one', game_type='door_native', is_active=True),
                Game(name='Reorder Cat Two Game', slug='reorder-cat-two-game',
                    category='reorder-cat-two', game_type='door_native', is_active=True),
            ])
            db.session.commit()
            c1_id, c2_id = c1.id, c2.id

        client = self._client()
        resp = client.post('/admin/games/categories/reorder', json={'order': [c2_id, c1_id]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

        with self.app.app_context():
            c1b = GameCategory.query.get(c1_id)
            c2b = GameCategory.query.get(c2_id)
            self.assertLess(c2b.sort_order, c1b.sort_order,
                            msg='Cat Two was posted first in the new order, '
                                'so it must end up with the lower sort_order')

    def test_reorder_categories_excludes_inline_categories(self):
        """Same real bug as test_renumber_categories_excludes_inline_
        categories, for the drag-and-drop endpoint instead of the
        one-click renumber: an inline category dragged into the middle
        of the posted order must not consume a number slot or have its
        own sort_order touched at all."""
        from anetbbs.models import db, Game, GameCategory
        with self.app.app_context():
            inline_cat = GameCategory(name='Inline Dragged', slug='reorder-inline-dragged',
                                      as_submenu=False, sort_order=99)
            sub_a = GameCategory(name='Drag Submenu A', slug='reorder-drag-submenu-a',
                                 as_submenu=True, sort_order=1)
            sub_b = GameCategory(name='Drag Submenu B', slug='reorder-drag-submenu-b',
                                 as_submenu=True, sort_order=2)
            db.session.add_all([inline_cat, sub_a, sub_b])
            db.session.add_all([
                Game(name='Drag Sub A Game', slug='reorder-drag-submenu-a-game',
                    category='reorder-drag-submenu-a', game_type='door_native', is_active=True),
                Game(name='Drag Sub B Game', slug='reorder-drag-submenu-b-game',
                    category='reorder-drag-submenu-b', game_type='door_native', is_active=True),
            ])
            db.session.commit()
            inline_id, sub_a_id, sub_b_id = inline_cat.id, sub_a.id, sub_b.id

        client = self._client()
        # Inline category dragged FIRST in the posted order -- must not
        # steal the "1" slot from the submenu category that follows it.
        resp = client.post('/admin/games/categories/reorder',
                           json={'order': [inline_id, sub_a_id, sub_b_id]})
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            inline_after = GameCategory.query.get(inline_id)
            sub_a_after = GameCategory.query.get(sub_a_id)
            sub_b_after = GameCategory.query.get(sub_b_id)
            self.assertEqual(inline_after.sort_order, 99,
                            msg="inline category's sort_order must be left untouched")
            self.assertEqual(sub_a_after.sort_order, 1)
            self.assertEqual(sub_b_after.sort_order, 2)

    def test_renumber_games_assigns_clean_gapped_sequence(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            db.session.add_all([
                Game(name='X', slug='renumber-x', category='other',
                    game_type='door_native', is_active=True, sort_order=20),
                Game(name='Y', slug='renumber-y', category='other',
                    game_type='door_native', is_active=True, sort_order=20),
            ])
            db.session.commit()

        client = self._client()
        resp = client.post('/admin/games/renumber', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            x = Game.query.filter_by(slug='renumber-x').first()
            y = Game.query.filter_by(slug='renumber-y').first()
            self.assertNotEqual(x.sort_order, y.sort_order,
                                msg='renumber must resolve the collision')

    def test_renumber_games_scoped_to_one_category_leaves_others_untouched(self):
        """Real bug reported live: a bulk-imported network category (17
        games, all left at Game.sort_order's own column default of 0)
        showed EVERY entry as "0." in its submenu, and only the first
        was actually reachable by number at all ("first claimant wins"
        on the rest). The ONLY existing fix tool was the GLOBAL
        renumber, which would also reshuffle every other category's own
        carefully-set custom numbers just to fix the one broken
        category. New `category` form field scopes the renumber to
        just one category, leaving every other game's sort_order
        completely untouched."""
        from anetbbs.models import db, Game
        with self.app.app_context():
            db.session.add_all([
                Game(name='Broken One', slug='scoped-renumber-broken-one',
                    category='scoped-renumber-cat', game_type='door_native',
                    is_active=True, sort_order=0),
                Game(name='Broken Two', slug='scoped-renumber-broken-two',
                    category='scoped-renumber-cat', game_type='door_native',
                    is_active=True, sort_order=0),
                Game(name='Unrelated', slug='scoped-renumber-unrelated',
                    category='other', game_type='door_native',
                    is_active=True, sort_order=777),
            ])
            db.session.commit()

        client = self._client()
        resp = client.post('/admin/games/renumber',
                           data={'category': 'scoped-renumber-cat'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            one = Game.query.filter_by(slug='scoped-renumber-broken-one').first()
            two = Game.query.filter_by(slug='scoped-renumber-broken-two').first()
            unrelated = Game.query.filter_by(slug='scoped-renumber-unrelated').first()
            self.assertNotEqual(one.sort_order, two.sort_order,
                                msg='the broken category\'s own collision must '
                                    'be resolved')
            self.assertEqual(unrelated.sort_order, 777,
                            msg="a game in a DIFFERENT category must be "
                                "completely untouched by a scoped renumber")

    def test_renumber_games_in_a_submenu_category_assigns_clean_sequence(self):
        """Real bug reported live: renumber_games() used to assign the
        GAPPED 10/20/30/... sequence unconditionally, even for games
        inside an as_submenu=True category -- contradicting
        resolve_sort_order_collisions()'s own every-boot policy of a
        clean 1/2/3/... run for exactly those games (nothing hardcodes
        a submenu game's number into ANSI art). A sysop editing a
        submenu game's number to 1 and clicking Renumber saw it jump
        straight back to 10."""
        from anetbbs.models import db, Game, GameCategory
        with self.app.app_context():
            db.session.add(GameCategory(name='Renumber Submenu Cat',
                                        slug='renumber-submenu-cat', as_submenu=True))
            db.session.add_all([
                Game(name='Sub One', slug='renumber-submenu-game-one',
                    category='renumber-submenu-cat', game_type='door_native',
                    is_active=True, sort_order=50),
                Game(name='Sub Two', slug='renumber-submenu-game-two',
                    category='renumber-submenu-cat', game_type='door_native',
                    is_active=True, sort_order=20),
            ])
            db.session.commit()

        client = self._client()
        resp = client.post('/admin/games/renumber',
                           data={'category': 'renumber-submenu-cat'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            two = Game.query.filter_by(slug='renumber-submenu-game-two').first()
            one = Game.query.filter_by(slug='renumber-submenu-game-one').first()
            self.assertEqual(two.sort_order, 1,
                            msg='lower pre-existing sort_order (20) must come first')
            self.assertEqual(one.sort_order, 2,
                            msg='submenu games must get a clean 1/2/3/... run, '
                                'not the gapped 10/20/30/... scheme')

    def test_reorder_games_in_a_submenu_category_writes_clean_sequence(self):
        """Same bug, for the drag-and-drop endpoint instead of the
        one-click renumber button."""
        from anetbbs.models import db, Game, GameCategory
        with self.app.app_context():
            db.session.add(GameCategory(name='Reorder Submenu Cat',
                                        slug='reorder-submenu-cat', as_submenu=True))
            a = Game(name='Drag A', slug='reorder-submenu-game-a',
                    category='reorder-submenu-cat', game_type='door_native',
                    is_active=True, sort_order=1)
            b = Game(name='Drag B', slug='reorder-submenu-game-b',
                    category='reorder-submenu-cat', game_type='door_native',
                    is_active=True, sort_order=2)
            db.session.add_all([a, b])
            db.session.commit()
            a_id, b_id = a.id, b.id

        client = self._client()
        resp = client.post('/admin/games/reorder', json={'order': [b_id, a_id]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

        with self.app.app_context():
            a2 = Game.query.get(a_id)
            b2 = Game.query.get(b_id)
            self.assertEqual(b2.sort_order, 1,
                            msg='B was posted first and must get the clean value 1')
            self.assertEqual(a2.sort_order, 2,
                            msg='submenu games must get a clean 1/2/3/... run, '
                                'not the gapped 10/20/30/... scheme')

    def test_renumber_categories_endpoint(self):
        """as_submenu=True AND at least one visible active game required
        here -- only a submenu category that actually shows a numbered
        stand-in line at all is included in this renumber (see
        renumber_categories()'s own docstring)."""
        from anetbbs.models import db, Game, GameCategory
        with self.app.app_context():
            db.session.add_all([
                GameCategory(name='Cat One', slug='renumber-cat-one',
                            as_submenu=True, sort_order=5),
                GameCategory(name='Cat Two', slug='renumber-cat-two',
                            as_submenu=True, sort_order=5),
            ])
            db.session.add_all([
                Game(name='Cat One Game', slug='renumber-cat-one-game',
                    category='renumber-cat-one', game_type='door_native', is_active=True),
                Game(name='Cat Two Game', slug='renumber-cat-two-game',
                    category='renumber-cat-two', game_type='door_native', is_active=True),
            ])
            db.session.commit()
        client = self._client()
        resp = client.post('/admin/games/categories/renumber', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            c1 = GameCategory.query.filter_by(slug='renumber-cat-one').first()
            c2 = GameCategory.query.filter_by(slug='renumber-cat-two').first()
            self.assertNotEqual(c1.sort_order, c2.sort_order)

    def test_renumber_categories_excludes_inline_categories(self):
        """Real bug reported live: an inline (as_submenu=False)
        category's own sort_order is never read for anything in the
        terminal door menu, but used to still consume a slot in the
        SAME sequential run as submenu categories -- "stealing" numbers
        from the ones that actually matter and producing a confusing,
        gap-riddled sequence for a sysop looking at the submenu
        categories alone. Fixed: only as_submenu=True categories are
        renumbered; an inline category's sort_order is left completely
        untouched."""
        from anetbbs.models import db, Game, GameCategory
        with self.app.app_context():
            inline_cat = GameCategory(name='Inline Early', slug='renumber-inline-early',
                                      as_submenu=False, sort_order=1)
            sub_a = GameCategory(name='Submenu A', slug='renumber-submenu-a',
                                 as_submenu=True, sort_order=2)
            sub_b = GameCategory(name='Submenu B', slug='renumber-submenu-b',
                                 as_submenu=True, sort_order=3)
            db.session.add_all([inline_cat, sub_a, sub_b])
            db.session.add_all([
                Game(name='Submenu A Game', slug='renumber-submenu-a-game',
                    category='renumber-submenu-a', game_type='door_native', is_active=True),
                Game(name='Submenu B Game', slug='renumber-submenu-b-game',
                    category='renumber-submenu-b', game_type='door_native', is_active=True),
            ])
            db.session.commit()
        client = self._client()
        resp = client.post('/admin/games/categories/renumber', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            inline_after = GameCategory.query.filter_by(slug='renumber-inline-early').first()
            sub_a_after = GameCategory.query.filter_by(slug='renumber-submenu-a').first()
            sub_b_after = GameCategory.query.filter_by(slug='renumber-submenu-b').first()
            # renumber_categories() is a GLOBAL operation (every
            # as_submenu=True populated category, not just these two --
            # unlike reorder_categories(), which is scoped to exactly
            # what's posted), and this test class shares one DB across
            # every method in it -- so relative ordering/uniqueness is
            # asserted here, not hardcoded absolute positions, which
            # would be sensitive to how many OTHER populated submenu
            # categories sibling tests in this same class have already
            # created.
            self.assertEqual(inline_after.sort_order, 1,
                            msg="inline category's sort_order must be left untouched")
            self.assertNotEqual(sub_a_after.sort_order, sub_b_after.sort_order,
                                msg='the two submenu categories must get distinct numbers')
            self.assertLess(sub_a_after.sort_order, sub_b_after.sort_order,
                            msg='Submenu A had the lower original value -- keeps the '
                                'earlier position')

    def test_collision_warning_flashed_on_edit(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            existing = Game(name='Existing', slug='collision-existing',
                            category='other', game_type='door_native',
                            is_active=True, sort_order=50)
            editable = Game(name='Editable', slug='collision-editable',
                            category='other', game_type='door_native',
                            is_active=True, sort_order=51)
            db.session.add_all([existing, editable])
            db.session.commit()
            editable_id = editable.id

        client = self._client()
        resp = client.post(
            f'/admin/games/{editable_id}/edit',
            data={
                'name': 'Editable', 'slug': 'collision-editable',
                'category': 'other', 'game_type': 'door_native',
                'max_nodes': '1', 'sort_order': '50',
                'is_active': 'y', 'drop_file_type': 'none',
            },
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('already used by', body)


class InProcessBuiltinPythonAppContextTests(unittest.TestCase):
    """Real bug reported live, over SSH (telnet/SSH/rlogin's in-process
    Game Center path): the SAME class of bug as
    test_builtin_runner_stdio_session_user_id.py's own
    RunPushesAppContextTests (the web UI's separate subprocess bridge),
    but in the OTHER launch path -- GameManager._launch()'s own
    in-process builtin_python branch (anetbbs/features/games.py) never
    pushed a Flask app context around calling the game's entry point
    either, so a builtin_python game that touches the database (The
    Shattered Realm's MudCharacter.query, the first one that actually
    needed this) crashed with "Working outside of application context"
    from telnet/SSH too, the instant it reached its first query -- not
    just from the web UI. Fixed with the same throwaway-Flask-app shape
    this function already builds further down for its own
    non-builtin_python branch."""

    def setUp(self):
        import sys
        import tempfile
        import types
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

        mod = types.ModuleType('fake_inprocess_db_game_mod')

        async def _launch(session, username):
            from anetbbs.models import User
            User.query.first()  # must not raise -- this is the whole point
            await session.write('db-ok')

        mod.launch = _launch
        sys.modules['fake_inprocess_db_game_mod'] = mod
        self.addCleanup(sys.modules.pop, 'fake_inprocess_db_game_mod', None)

        db.session.add(Game(
            name='DB Touching Game', slug='db-touching-game-ctx-test',
            category='other', game_type='builtin_python', is_active=True,
            sort_order=8800, web_game_module='fake_inprocess_db_game_mod:launch'))
        db.session.commit()

    def test_builtin_python_game_can_touch_the_database_over_ssh_path(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['8800', 'Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = session.transcript()
        self.assertNotIn('Working outside of application context', txt)
        self.assertIn('db-ok', txt)


class ResolveSortOrderCollisionsTests(unittest.TestCase):
    """Real bug reported live (screenshot): a bulk-imported network
    category (ADVENTURE, 122 games) left every single game at
    Game.sort_order's own column default of 0 -- every entry in that
    category's submenu showed as "0.", and only the first was ever
    reachable by number at all ("first claimant wins" on the rest).
    resolve_sort_order_collisions() is the self-healing fix, run
    automatically on every boot (web_app.py's create_app(), right
    after _create_default_data()) -- these tests call it directly."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def test_games_sharing_zero_sort_order_in_one_category_get_resolved(self):
        """as_submenu=True required here -- a category's submenu page
        gets the always-resequence treatment (see this function's own
        docstring); an inline/uncategorized collision only gets the
        more conservative bump-the-duplicates fix instead."""
        from anetbbs.models import db, Game, GameCategory
        db.session.add(GameCategory(name='Collide Cat', slug='collide-cat', as_submenu=True))
        db.session.add_all([
            Game(name='Collide A', slug='collide-a', category='collide-cat',
                game_type='door_native', is_active=True, sort_order=0),
            Game(name='Collide B', slug='collide-b', category='collide-cat',
                game_type='door_native', is_active=True, sort_order=0),
            Game(name='Collide C', slug='collide-c', category='collide-cat',
                game_type='door_native', is_active=True, sort_order=0),
        ])
        db.session.commit()
        from anetbbs.features.games import resolve_sort_order_collisions

        changed = resolve_sort_order_collisions()
        self.assertTrue(changed)

        orders = {g.sort_order for g in
                 Game.query.filter_by(category='collide-cat').all()}
        self.assertEqual(orders, {1, 2, 3},
                         msg='real live feedback: a patched-up-but-gappy result '
                             '(e.g. 0, 10, 20) is just as unwelcome as the original '
                             'collision -- a colliding category gets a full clean '
                             '1, 2, 3, ... resequence, not just distinct values')

    def test_a_collision_resequences_the_whole_category_including_its_already_unique_game(self):
        """Exact shape of the real live bug (ADVENTURE, 122 games): one
        bundled door (e.g. ANetCRAFT) already had a real, unique,
        non-colliding sort_order from its own seed data, while every
        bulk-imported game in the same category was stuck at the
        column's own 0 default. The OLD per-duplicate-only fix
        preserved that one unique value and bumped only the
        duplicates, producing an ugly "0, 1, 11, 21, 31, ..." result
        -- real feedback was this is still "crazy" looking. A submenu
        category's games always get the full clean resequence now
        (collision or not -- see this function's own docstring), which
        includes this exact case."""
        from anetbbs.models import db, Game, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add(GameCategory(name='Mixed Collide Cat', slug='mixed-collide-cat',
                                    as_submenu=True))
        db.session.add(Game(name='Already Unique', slug='already-unique-game',
                           category='mixed-collide-cat', game_type='door_native',
                           is_active=True, sort_order=1))
        db.session.add_all([
            Game(name=f'Colliding {i}', slug=f'mixed-colliding-{i}',
                category='mixed-collide-cat', game_type='door_native',
                is_active=True, sort_order=0)
            for i in range(4)
        ])
        db.session.commit()

        resolve_sort_order_collisions()

        orders = sorted(g.sort_order for g in
                        Game.query.filter_by(category='mixed-collide-cat').all())
        self.assertEqual(orders, [1, 2, 3, 4, 5])

    def test_games_in_different_categories_do_not_collide_with_each_other(self):
        """A 0 in category A and a 0 in category B are not a collision
        with each other -- collisions are only ever resolved within
        the same category, matching how numbering actually works."""
        from anetbbs.models import db, Game
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add_all([
            Game(name='Solo A', slug='solo-a', category='solo-cat-a',
                game_type='door_native', is_active=True, sort_order=0),
            Game(name='Solo B', slug='solo-b', category='solo-cat-b',
                game_type='door_native', is_active=True, sort_order=0),
        ])
        db.session.commit()

        resolve_sort_order_collisions()

        a = Game.query.filter_by(slug='solo-a').first()
        b = Game.query.filter_by(slug='solo-b').first()
        self.assertEqual(a.sort_order, 0, msg='no collision in its own category -- left alone')
        self.assertEqual(b.sort_order, 0, msg='no collision in its own category -- left alone')

    def test_non_submenu_games_with_unique_but_gappy_numbers_are_never_touched(self):
        """A game NOT inside an as_submenu category (here: category
        'other', with no matching GameCategory row at all) keeps the
        strict collision-only rule -- its number can be hardcoded into
        the TOP-LEVEL door-menu art screen (see show_door_menu()'s own
        big comment), so a unique-but-gappy value must be left
        completely alone. That needs an explicit sysop "Renumber"
        click, not a silent auto-rewrite."""
        from anetbbs.models import db, Game
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add(Game(name='Gappy Game', slug='gappy-game',
                           category='other', game_type='door_native',
                           is_active=True, sort_order=135))
        db.session.commit()

        changed = resolve_sort_order_collisions()
        self.assertFalse(changed)

        g = Game.query.filter_by(slug='gappy-game').first()
        self.assertEqual(g.sort_order, 135)

    def test_submenu_games_with_unique_but_gappy_numbers_are_resequenced_anyway(self):
        """Exact shape of the real live bug (ADULT, CHAT/Apps/Other):
        these categories had NO literal collision (every game already
        had a distinct sort_order) but were left gappy -- "0, 10, 20,
        30, ..." -- the result of an EARLIER version of this function,
        which only ever bumped genuine duplicates and left an
        already-unique sequence alone. Since Jerry has confirmed no
        existing submenu ANSI art depends on specific numbers, a
        submenu category's games now ALWAYS resequence to a clean
        1, 2, 3, ... run, collision or not -- otherwise an
        already-collision-free-but-gappy category could never reach a
        clean state at all."""
        from anetbbs.models import db, Game, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add(GameCategory(name='Gappy Submenu Cat', slug='gappy-submenu-cat',
                                    as_submenu=True))
        db.session.add_all([
            Game(name='Gappy Sub A', slug='gappy-submenu-a', category='gappy-submenu-cat',
                game_type='door_native', is_active=True, sort_order=0),
            Game(name='Gappy Sub B', slug='gappy-submenu-b', category='gappy-submenu-cat',
                game_type='door_native', is_active=True, sort_order=10),
            Game(name='Gappy Sub C', slug='gappy-submenu-c', category='gappy-submenu-cat',
                game_type='door_native', is_active=True, sort_order=20),
        ])
        db.session.commit()

        changed = resolve_sort_order_collisions()
        self.assertTrue(changed)

        orders = sorted(g.sort_order for g in
                        Game.query.filter_by(category='gappy-submenu-cat').all())
        self.assertEqual(orders, [1, 2, 3])

    def test_populated_submenu_categories_are_always_resequenced_to_start_at_one(self):
        """Real live feedback across several rounds ("numbers should
        start at 1"): unlike games, a populated submenu category's
        number is NOT something a sysop hardcodes into per-door ANSI
        art, so it's always resequenced to a clean 1, 2, 3, ... run on
        every boot -- even when its current value is already unique,
        just gap-riddled (e.g. left over from before the category
        drag-and-drop fix existed). Relative order (by current
        sort_order, name) is preserved; only the NUMBER changes."""
        from anetbbs.models import db, Game, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add_all([
            GameCategory(name='Gappy A', slug='gappy-cat-a',
                        as_submenu=True, sort_order=20),
            GameCategory(name='Gappy B', slug='gappy-cat-b',
                        as_submenu=True, sort_order=70),
        ])
        db.session.add_all([
            Game(name='Gappy A Game', slug='gappy-cat-a-game',
                category='gappy-cat-a', game_type='door_native', is_active=True),
            Game(name='Gappy B Game', slug='gappy-cat-b-game',
                category='gappy-cat-b', game_type='door_native', is_active=True),
        ])
        db.session.commit()

        changed = resolve_sort_order_collisions()
        self.assertTrue(changed)

        a = GameCategory.query.filter_by(slug='gappy-cat-a').first()
        b = GameCategory.query.filter_by(slug='gappy-cat-b').first()
        self.assertEqual(a.sort_order, 1,
                        msg='lower original value (20) keeps the earlier position -> 1')
        self.assertEqual(b.sort_order, 2)

    def test_empty_submenu_category_is_never_assigned_a_number(self):
        """A submenu category with zero visible active games never
        renders a numbered stand-in line in the terminal menu at all --
        assigning it a number here would just silently waste a slot the
        same way an inline category used to."""
        from anetbbs.models import db, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add(GameCategory(name='Empty Cat', slug='empty-cat-test',
                                    as_submenu=True, sort_order=42))
        db.session.commit()

        changed = resolve_sort_order_collisions()
        self.assertFalse(changed)

        c = GameCategory.query.filter_by(slug='empty-cat-test').first()
        self.assertEqual(c.sort_order, 42)

    def test_category_whose_only_game_is_builtin_web_is_never_assigned_a_number(self):
        """Real bug reported live (screenshot): the top-level Door Games
        list had unexplained gaps (e.g. "...12, [missing 13], 14...").
        Root cause: a category whose only game is game_type
        'builtin_web' or 'door_dos_browser' never actually renders a
        numbered line in the terminal menu at all -- show_door_menu()'s
        own game_list query excludes those two types entirely -- but an
        earlier version of this function counted it as "visible" anyway
        (a plain is_active=True check, no type filter) and handed it a
        number that then silently vanished from the sequence a sysop
        actually sees on screen."""
        from anetbbs.models import db, Game, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add(GameCategory(name='Web Only Cat', slug='web-only-cat-test',
                                    as_submenu=True, sort_order=99))
        db.session.add(Game(name='Web Only Game', slug='web-only-game-test',
                           category='web-only-cat-test', game_type='builtin_web',
                           is_active=True))
        db.session.commit()

        changed = resolve_sort_order_collisions()
        self.assertFalse(changed)

        c = GameCategory.query.filter_by(slug='web-only-cat-test').first()
        self.assertEqual(c.sort_order, 99,
                         msg='a category whose only game never renders in the '
                             'terminal menu must not be numbered at all')

    def test_submenu_categories_sharing_a_sort_order_get_resolved(self):
        from anetbbs.models import db, Game, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add_all([
            GameCategory(name='Collide Cat A', slug='collide-cat-a',
                        as_submenu=True, sort_order=5),
            GameCategory(name='Collide Cat B', slug='collide-cat-b',
                        as_submenu=True, sort_order=5),
        ])
        db.session.add_all([
            Game(name='Collide Cat A Game', slug='collide-cat-a-game',
                category='collide-cat-a', game_type='door_native', is_active=True),
            Game(name='Collide Cat B Game', slug='collide-cat-b-game',
                category='collide-cat-b', game_type='door_native', is_active=True),
        ])
        db.session.commit()

        resolve_sort_order_collisions()

        a = GameCategory.query.filter_by(slug='collide-cat-a').first()
        b = GameCategory.query.filter_by(slug='collide-cat-b').first()
        self.assertNotEqual(a.sort_order, b.sort_order)

    def test_inline_categories_are_never_touched(self):
        """An inline category's own sort_order is never read for
        anything in the terminal menu at all (see reorder_categories()'s
        docstring) -- it must never be reassigned, colliding or not."""
        from anetbbs.models import db, GameCategory
        from anetbbs.features.games import resolve_sort_order_collisions
        db.session.add_all([
            GameCategory(name='Inline Collide A', slug='inline-collide-a',
                        as_submenu=False, sort_order=3),
            GameCategory(name='Inline Collide B', slug='inline-collide-b',
                        as_submenu=False, sort_order=3),
        ])
        db.session.commit()

        resolve_sort_order_collisions()

        a = GameCategory.query.filter_by(slug='inline-collide-a').first()
        b = GameCategory.query.filter_by(slug='inline-collide-b').first()
        self.assertEqual(a.sort_order, 3)
        self.assertEqual(b.sort_order, 3)


if __name__ == '__main__':
    unittest.main()
