"""Model-level tests for The Shattered Realm (Phase 5 of the
door-games roadmap, 2026-10-20): MudRoom/MudExit/MudItemTemplate/
MudMobTemplate/MudCharacter -- relationships, uniqueness constraints,
and FK configuration.
"""
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


class MudModelsTests(unittest.TestCase):
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

    def test_room_exit_relationship_resolves_target(self):
        from anetbbs.models import db, MudRoom, MudExit
        a = MudRoom(slug='room-a', name='Room A', description='A.')
        b = MudRoom(slug='room-b', name='Room B', description='B.')
        db.session.add_all([a, b])
        db.session.commit()
        db.session.add(MudExit(room_id=a.id, direction='north', target_room_id=b.id))
        db.session.commit()
        self.assertEqual(a.exits.count(), 1)
        self.assertEqual(a.exits.first().target_room.name, 'Room B')

    def test_exit_is_not_automatically_bidirectional(self):
        from anetbbs.models import db, MudRoom, MudExit
        a = MudRoom(slug='room-a', name='Room A', description='A.')
        b = MudRoom(slug='room-b', name='Room B', description='B.')
        db.session.add_all([a, b])
        db.session.commit()
        db.session.add(MudExit(room_id=a.id, direction='north', target_room_id=b.id))
        db.session.commit()
        self.assertEqual(b.exits.count(), 0)

    def test_duplicate_direction_same_room_is_rejected(self):
        from anetbbs.models import db, MudRoom, MudExit
        from sqlalchemy.exc import IntegrityError
        a = MudRoom(slug='room-a', name='Room A', description='A.')
        b = MudRoom(slug='room-b', name='Room B', description='B.')
        c = MudRoom(slug='room-c', name='Room C', description='C.')
        db.session.add_all([a, b, c])
        db.session.commit()
        db.session.add(MudExit(room_id=a.id, direction='north', target_room_id=b.id))
        db.session.commit()
        db.session.add(MudExit(room_id=a.id, direction='north', target_room_id=c.id))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_room_mob_template_relationship(self):
        from anetbbs.models import db, MudRoom, MudMobTemplate
        mob = MudMobTemplate(slug='rat', name='Rat', level=1, hp=5,
                             attack=1, defense=0, xp_reward=2, gold_reward=1)
        db.session.add(mob)
        db.session.commit()
        room = MudRoom(slug='room-a', name='Room A', description='A.',
                       mob_template_id=mob.id)
        db.session.add(room)
        db.session.commit()
        self.assertEqual(room.mob_template.name, 'Rat')

    def test_character_one_per_user(self):
        from anetbbs.models import db, User, MudCharacter
        from sqlalchemy.exc import IntegrityError
        u = User(username='dup', email='dup@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        db.session.add(MudCharacter(user_id=u.id, name='First'))
        db.session.commit()
        db.session.add(MudCharacter(user_id=u.id, name='Second'))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_character_default_stats(self):
        from anetbbs.models import db, User, MudCharacter
        u = User(username='newbie', email='newbie@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        char = MudCharacter(user_id=u.id, name='Newbie')
        db.session.add(char)
        db.session.commit()
        self.assertEqual(char.level, 1)
        self.assertEqual(char.hp, 20)
        self.assertEqual(char.hp_max, 20)
        self.assertEqual(char.attack, 4)
        self.assertEqual(char.defense, 1)
        self.assertEqual(char.gold, 10)
        self.assertEqual(char.xp_next, 20)
        self.assertEqual(char.inventory_json, '[]')

    def test_user_id_fk_declares_ondelete_cascade(self):
        """Matches this codebase's own established convention
        (MeadowlarkSave/DarkForcesSave use the identical shape):
        `ondelete='CASCADE'` on the column is a real DB-level directive
        for a backend that enforces it, but SQLite doesn't enable FK
        enforcement by default and the ORM's own relationship (a plain
        backref, no passive_deletes=True) nullifies the child's FK
        on an ORM-level session.delete(parent) instead of cascading --
        the exact same (pre-existing, codebase-wide) characteristic
        MeadowlarkSave/DarkForcesSave already have, not something new
        to this model. This test confirms the declared intent (the
        column-level directive) rather than asserting a live ORM
        cascade this codebase doesn't actually guarantee anywhere."""
        from anetbbs.models import MudCharacter
        fk = list(MudCharacter.__table__.c.user_id.foreign_keys)[0]
        self.assertEqual(fk.ondelete, 'CASCADE')

    def test_item_template_slug_unique(self):
        from anetbbs.models import db, MudItemTemplate
        from sqlalchemy.exc import IntegrityError
        db.session.add(MudItemTemplate(slug='sword', name='Sword'))
        db.session.commit()
        db.session.add(MudItemTemplate(slug='sword', name='Another Sword'))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_room_feature_field_defaults_to_none(self):
        from anetbbs.models import db, MudRoom
        room = MudRoom(slug='plain-room', name='Plain', description='.')
        db.session.add(room)
        db.session.commit()
        self.assertIsNone(room.feature)


if __name__ == '__main__':
    unittest.main()
