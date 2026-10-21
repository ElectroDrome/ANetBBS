"""Tests for anetbbs/features/mud/seed.py (idempotent world seeding)
and the Game/GameCategory registration in web_app.py's
_create_default_data(). Also formalizes the room/exit graph-
consistency checks done manually during development (every exit
target exists, every room is reachable from the starting room)."""
import os
import sys
import unittest
from pathlib import Path

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


class MudSeedDataConsistencyTests(unittest.TestCase):
    """Pure-data checks, no DB needed -- confirms the ROOMS/EXITS
    tables themselves are internally consistent before they ever reach
    the database."""

    def test_every_exit_references_real_rooms(self):
        from anetbbs.features.mud.seed import ROOMS, EXITS
        room_slugs = {r[0] for r in ROOMS}
        for room_slug, direction, target_slug in EXITS:
            self.assertIn(room_slug, room_slugs, f'unknown source room: {room_slug}')
            self.assertIn(target_slug, room_slugs, f'unknown target room: {target_slug}')

    def test_every_room_mob_slug_is_a_real_mob(self):
        from anetbbs.features.mud.seed import ROOMS, MOBS
        mob_slugs = {m[0] for m in MOBS}
        for slug, zone, name, desc, mob_slug, feature in ROOMS:
            if mob_slug is not None:
                self.assertIn(mob_slug, mob_slugs, f'room {slug} references unknown mob {mob_slug}')

    def test_every_room_is_reachable_from_the_starting_room(self):
        from anetbbs.features.mud.seed import ROOMS, EXITS
        from anetbbs.features.mud import STARTING_ROOM_SLUG
        room_slugs = {r[0] for r in ROOMS}
        graph = {}
        for room_slug, direction, target_slug in EXITS:
            graph.setdefault(room_slug, []).append(target_slug)
        seen, stack = set(), [STARTING_ROOM_SLUG]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(graph.get(cur, []))
        unreachable = room_slugs - seen
        self.assertEqual(unreachable, set(),
                         f'rooms unreachable from {STARTING_ROOM_SLUG}: {unreachable}')

    def test_no_duplicate_room_slugs(self):
        from anetbbs.features.mud.seed import ROOMS
        slugs = [r[0] for r in ROOMS]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_no_duplicate_direction_within_one_room(self):
        from anetbbs.features.mud.seed import EXITS
        seen = set()
        for room_slug, direction, target_slug in EXITS:
            key = (room_slug, direction)
            self.assertNotIn(key, seen, f'duplicate exit direction: {key}')
            seen.add(key)

    def test_shop_and_healer_features_each_appear_at_least_once(self):
        from anetbbs.features.mud.seed import ROOMS
        features = {r[5] for r in ROOMS}
        self.assertIn('shop', features)
        self.assertIn('healer', features)

    def test_every_quest_giver_room_and_target_mob_are_real(self):
        from anetbbs.features.mud.seed import ROOMS, MOBS, QUESTS
        room_slugs = {r[0] for r in ROOMS}
        mob_slugs = {m[0] for m in MOBS}
        for (slug, name, giver_room_slug, target_mob_slug, target_count,
            reward_xp, reward_gold, reward_item_slug) in QUESTS:
            self.assertIn(giver_room_slug, room_slugs,
                         f'quest {slug} references unknown giver room')
            self.assertIn(target_mob_slug, mob_slugs,
                         f'quest {slug} references unknown target mob')

    def test_every_class_restricted_item_names_a_real_class(self):
        from anetbbs.features.mud.seed import ITEMS
        from anetbbs.features.mud.classes import CLASS_SLUGS
        for (slug, name, item_type, atk, dfn, heal, value, stock, desc,
            required_class) in ITEMS:
            if required_class is not None:
                self.assertIn(required_class, CLASS_SLUGS,
                             f'item {slug} requires unknown class {required_class}')


class MudSeedDatabaseTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        # create_app() already calls seed_world() once via
        # _create_default_data() -- these tests confirm that already
        # happened AND that calling it again is a safe no-op.

    def test_rooms_items_mobs_already_seeded_by_create_app(self):
        from anetbbs.models import MudRoom, MudItemTemplate, MudMobTemplate
        from anetbbs.features.mud.seed import ROOMS, ITEMS, MOBS
        self.assertEqual(MudRoom.query.count(), len(ROOMS))
        self.assertEqual(MudItemTemplate.query.count(), len(ITEMS))
        self.assertEqual(MudMobTemplate.query.count(), len(MOBS))

    def test_calling_seed_world_again_is_idempotent(self):
        from anetbbs.models import MudRoom, MudItemTemplate, MudMobTemplate, MudExit
        from anetbbs.features.mud.seed import seed_world
        before = (MudRoom.query.count(), MudItemTemplate.query.count(),
                  MudMobTemplate.query.count(), MudExit.query.count())
        seed_world()
        seed_world()
        after = (MudRoom.query.count(), MudItemTemplate.query.count(),
                 MudMobTemplate.query.count(), MudExit.query.count())
        self.assertEqual(before, after)

    def test_starting_room_exists_and_is_in_the_village_zone(self):
        from anetbbs.models import MudRoom
        from anetbbs.features.mud import STARTING_ROOM_SLUG
        room = MudRoom.query.filter_by(slug=STARTING_ROOM_SLUG).first()
        self.assertIsNotNone(room)
        self.assertEqual(room.zone, 'village')

    def test_game_row_registered_as_builtin_python(self):
        from anetbbs.models import Game
        game = Game.query.filter_by(slug='shattered-realm').first()
        self.assertIsNotNone(game)
        self.assertEqual(game.game_type, 'builtin_python')
        self.assertEqual(game.web_game_module, 'anetbbs.features.mud:launch_mud')
        self.assertTrue(game.is_active)

    def test_mud_game_category_exists(self):
        from anetbbs.models import GameCategory
        self.assertIsNotNone(GameCategory.query.filter_by(slug='mud').first())

    def test_game_sort_order_does_not_collide_with_anetcraft(self):
        from anetbbs.models import Game
        anetcraft = Game.query.filter_by(slug='anetcraft').first()
        mud = Game.query.filter_by(slug='shattered-realm').first()
        if anetcraft is not None and mud is not None:
            self.assertNotEqual(anetcraft.sort_order, mud.sort_order)

    def test_quest_templates_seeded_with_resolved_giver_room(self):
        from anetbbs.models import MudQuestTemplate, MudRoom
        quest = MudQuestTemplate.query.filter_by(slug='cleanse-the-citadel').first()
        self.assertIsNotNone(quest)
        giver = MudRoom.query.get(quest.giver_room_id)
        self.assertEqual(giver.slug, 'village-chapel')
        self.assertEqual(quest.target_mob_slug, 'wraith')

    def test_citadel_zone_items_have_required_class_set_in_db(self):
        from anetbbs.models import MudItemTemplate
        item = MudItemTemplate.query.filter_by(slug='archmage-staff').first()
        self.assertIsNotNone(item)
        self.assertEqual(item.required_class, 'mage')


if __name__ == '__main__':
    unittest.main()
