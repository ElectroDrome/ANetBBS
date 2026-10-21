"""Tests for anetbbs/web/mud_admin.py -- the Rooms/Exits/Items/Mobs
CRUD routes, same shape as the existing games_admin/menu_admin route
tests."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class MudAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.mud_admin_test.db')
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
            admin = User(username='mudadmintest', email='mudadmintest@example.com',
                        password_hash='x', is_admin=True)
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

    def test_index_shows_counts(self):
        resp = self._client().get('/admin/mud/')
        self.assertEqual(resp.status_code, 200)

    # --- Rooms -------------------------------------------------------

    def test_create_room(self):
        from anetbbs.models import MudRoom
        client = self._client()
        resp = client.post('/admin/mud/rooms/new', data={
            'slug': 'admin-test-room', 'name': 'Admin Test Room',
            'zone': 'village', 'description': 'A room made by a test.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            self.assertIsNotNone(MudRoom.query.filter_by(slug='admin-test-room').first())

    def test_create_room_duplicate_slug_is_refused(self):
        client = self._client()
        client.post('/admin/mud/rooms/new', data={
            'slug': 'dup-room', 'name': 'Dup Room', 'description': '.'})
        resp = client.post('/admin/mud/rooms/new', data={
            'slug': 'dup-room', 'name': 'Dup Room Again', 'description': '.'},
            follow_redirects=True)
        self.assertIn('already exists', resp.get_data(as_text=True))

    def test_edit_room(self):
        from anetbbs.models import MudRoom
        client = self._client()
        client.post('/admin/mud/rooms/new', data={
            'slug': 'editable-room', 'name': 'Before Edit', 'description': '.'})
        with self.app.app_context():
            room_id = MudRoom.query.filter_by(slug='editable-room').first().id
        resp = client.post(f'/admin/mud/rooms/{room_id}/edit', data={
            'name': 'After Edit', 'zone': 'forest', 'description': 'Changed.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            room = MudRoom.query.get(room_id)
            self.assertEqual(room.name, 'After Edit')
            self.assertEqual(room.zone, 'forest')

    def test_delete_room_also_deletes_its_exits(self):
        from anetbbs.models import db, MudRoom, MudExit
        client = self._client()
        with self.app.app_context():
            a = MudRoom(slug='del-room-a', name='A', description='.')
            b = MudRoom(slug='del-room-b', name='B', description='.')
            db.session.add_all([a, b])
            db.session.commit()
            db.session.add(MudExit(room_id=a.id, direction='north', target_room_id=b.id))
            db.session.commit()
            a_id, exit_count_before = a.id, MudExit.query.count()
        client.post(f'/admin/mud/rooms/{a_id}/delete', follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(MudRoom.query.get(a_id))
            self.assertLess(MudExit.query.count(), exit_count_before)

    def test_add_exit_and_delete_exit(self):
        from anetbbs.models import db, MudRoom, MudExit
        client = self._client()
        with self.app.app_context():
            a = MudRoom(slug='exit-room-a', name='A', description='.')
            b = MudRoom(slug='exit-room-b', name='B', description='.')
            db.session.add_all([a, b])
            db.session.commit()
            a_id, b_id = a.id, b.id
        client.post(f'/admin/mud/rooms/{a_id}/exits/new', data={
            'direction': 'south', 'target_room_id': str(b_id),
        }, follow_redirects=True)
        with self.app.app_context():
            exit_row = MudExit.query.filter_by(room_id=a_id, direction='south').first()
            self.assertIsNotNone(exit_row)
            exit_id = exit_row.id
        client.post(f'/admin/mud/rooms/{a_id}/exits/{exit_id}/delete',
                   follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(MudExit.query.get(exit_id))

    def test_duplicate_direction_on_same_room_is_refused(self):
        from anetbbs.models import db, MudRoom
        client = self._client()
        with self.app.app_context():
            a = MudRoom(slug='dupexit-room-a', name='A', description='.')
            b = MudRoom(slug='dupexit-room-b', name='B', description='.')
            c = MudRoom(slug='dupexit-room-c', name='C', description='.')
            db.session.add_all([a, b, c])
            db.session.commit()
            a_id, b_id, c_id = a.id, b.id, c.id
        client.post(f'/admin/mud/rooms/{a_id}/exits/new',
                   data={'direction': 'east', 'target_room_id': str(b_id)})
        resp = client.post(f'/admin/mud/rooms/{a_id}/exits/new',
                          data={'direction': 'east', 'target_room_id': str(c_id)},
                          follow_redirects=True)
        self.assertIn('already has a', resp.get_data(as_text=True))

    # --- Items ---------------------------------------------------------

    def test_create_and_edit_item(self):
        from anetbbs.models import MudItemTemplate
        client = self._client()
        client.post('/admin/mud/items/new', data={
            'slug': 'admin-test-item', 'name': 'Admin Sword',
            'item_type': 'weapon', 'attack_bonus': '4', 'value': '15',
        }, follow_redirects=True)
        with self.app.app_context():
            item = MudItemTemplate.query.filter_by(slug='admin-test-item').first()
            self.assertIsNotNone(item)
            self.assertEqual(item.attack_bonus, 4)
            item_id = item.id
        client.post(f'/admin/mud/items/{item_id}/edit', data={
            'name': 'Admin Sword+1', 'item_type': 'weapon',
            'attack_bonus': '5', 'value': '20',
        }, follow_redirects=True)
        with self.app.app_context():
            item = MudItemTemplate.query.get(item_id)
            self.assertEqual(item.name, 'Admin Sword+1')
            self.assertEqual(item.attack_bonus, 5)

    def test_delete_item(self):
        from anetbbs.models import MudItemTemplate
        client = self._client()
        client.post('/admin/mud/items/new', data={
            'slug': 'deletable-item', 'name': 'Deletable'})
        with self.app.app_context():
            item_id = MudItemTemplate.query.filter_by(slug='deletable-item').first().id
        client.post(f'/admin/mud/items/{item_id}/delete', follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(MudItemTemplate.query.get(item_id))

    # --- Mobs ------------------------------------------------------------

    def test_create_and_edit_mob(self):
        from anetbbs.models import MudMobTemplate
        client = self._client()
        client.post('/admin/mud/mobs/new', data={
            'slug': 'admin-test-mob', 'name': 'Admin Slime',
            'level': '2', 'hp': '12', 'attack': '3', 'defense': '1',
            'xp_reward': '9', 'gold_reward': '4',
        }, follow_redirects=True)
        with self.app.app_context():
            mob = MudMobTemplate.query.filter_by(slug='admin-test-mob').first()
            self.assertIsNotNone(mob)
            self.assertEqual(mob.hp, 12)
            mob_id = mob.id
        client.post(f'/admin/mud/mobs/{mob_id}/edit', data={
            'name': 'Admin Slime', 'level': '2', 'hp': '20',
            'attack': '3', 'defense': '1', 'xp_reward': '9', 'gold_reward': '4',
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(MudMobTemplate.query.get(mob_id).hp, 20)

    def test_mob_in_use_by_a_room_cannot_be_deleted(self):
        from anetbbs.models import db, MudMobTemplate, MudRoom
        client = self._client()
        with self.app.app_context():
            mob = MudMobTemplate(slug='in-use-mob', name='In Use Mob',
                                 level=1, hp=5, attack=1, defense=0,
                                 xp_reward=1, gold_reward=1)
            db.session.add(mob)
            db.session.commit()
            room = MudRoom(slug='mob-room', name='Mob Room', description='.',
                           mob_template_id=mob.id)
            db.session.add(room)
            db.session.commit()
            mob_id = mob.id
        resp = client.post(f'/admin/mud/mobs/{mob_id}/delete', follow_redirects=True)
        self.assertIn('Cannot delete', resp.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(MudMobTemplate.query.get(mob_id))

    # --- Items: required_class ---------------------------------------------

    def test_item_required_class_round_trips(self):
        from anetbbs.models import MudItemTemplate
        client = self._client()
        client.post('/admin/mud/items/new', data={
            'slug': 'admin-class-item', 'name': 'Admin Staff',
            'item_type': 'weapon', 'required_class': 'mage',
        }, follow_redirects=True)
        with self.app.app_context():
            item = MudItemTemplate.query.filter_by(slug='admin-class-item').first()
            self.assertEqual(item.required_class, 'mage')
            item_id = item.id
        # Blank selection clears it back to "any class".
        client.post(f'/admin/mud/items/{item_id}/edit', data={
            'name': 'Admin Staff', 'item_type': 'weapon', 'required_class': '',
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(MudItemTemplate.query.get(item_id).required_class)

    # --- Quests --------------------------------------------------------

    def _make_quest_prereqs(self):
        from anetbbs.models import db, MudMobTemplate, MudRoom
        mob = MudMobTemplate.query.filter_by(slug='quest-admin-mob').first()
        if mob is None:
            mob = MudMobTemplate(slug='quest-admin-mob', name='Quest Mob',
                                 level=1, hp=5, attack=1, defense=0,
                                 xp_reward=1, gold_reward=1)
            db.session.add(mob)
        room = MudRoom.query.filter_by(slug='quest-admin-room').first()
        if room is None:
            room = MudRoom(slug='quest-admin-room', name='Quest Room',
                           description='.')
            db.session.add(room)
        db.session.commit()
        return mob.slug, room.id

    def test_create_and_edit_quest(self):
        from anetbbs.models import MudQuestTemplate
        client = self._client()
        with self.app.app_context():
            mob_slug, room_id = self._make_quest_prereqs()
        client.post('/admin/mud/quests/new', data={
            'slug': 'admin-test-quest', 'name': 'Admin Quest',
            'giver_room_id': str(room_id), 'target_mob_slug': mob_slug,
            'target_count': '2', 'reward_xp': '10', 'reward_gold': '5',
        }, follow_redirects=True)
        with self.app.app_context():
            quest = MudQuestTemplate.query.filter_by(slug='admin-test-quest').first()
            self.assertIsNotNone(quest)
            self.assertEqual(quest.target_count, 2)
            quest_id = quest.id
        client.post(f'/admin/mud/quests/{quest_id}/edit', data={
            'name': 'Admin Quest Renamed', 'giver_room_id': str(room_id),
            'target_mob_slug': mob_slug, 'target_count': '3',
            'reward_xp': '10', 'reward_gold': '5',
        }, follow_redirects=True)
        with self.app.app_context():
            quest = MudQuestTemplate.query.get(quest_id)
            self.assertEqual(quest.name, 'Admin Quest Renamed')
            self.assertEqual(quest.target_count, 3)

    def test_new_quest_without_a_giver_room_is_refused(self):
        from anetbbs.models import MudQuestTemplate
        client = self._client()
        with self.app.app_context():
            mob_slug, _room_id = self._make_quest_prereqs()
        resp = client.post('/admin/mud/quests/new', data={
            'slug': 'no-giver-quest', 'name': 'No Giver Quest',
            'target_mob_slug': mob_slug, 'target_count': '1',
        }, follow_redirects=True)
        self.assertIn('giver room is required', resp.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(MudQuestTemplate.query.filter_by(slug='no-giver-quest').first())

    def test_delete_quest(self):
        from anetbbs.models import MudQuestTemplate
        client = self._client()
        with self.app.app_context():
            mob_slug, room_id = self._make_quest_prereqs()
        client.post('/admin/mud/quests/new', data={
            'slug': 'deletable-quest', 'name': 'Deletable Quest',
            'giver_room_id': str(room_id), 'target_mob_slug': mob_slug,
            'target_count': '1',
        }, follow_redirects=True)
        with self.app.app_context():
            quest_id = MudQuestTemplate.query.filter_by(slug='deletable-quest').first().id
        client.post(f'/admin/mud/quests/{quest_id}/delete', follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(MudQuestTemplate.query.get(quest_id))


if __name__ == '__main__':
    unittest.main()
