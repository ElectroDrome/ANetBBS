"""Tests for anetbbs/features/mud/parser.py -- the command dispatch
table (verbs + abbreviations), movement/look, inventory/equip,
chat, and the shop/healer room-feature gates. Built from scratch
(confirmed: no multi-word command parser precedent existed anywhere
else in this codebase)."""
import asyncio
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


class _FakeSession:
    def __init__(self, user=None):
        self.user = user or {'username': 'parsertest'}
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return ''

    def transcript(self):
        return ''.join(self.written)


class MudParserTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User, MudCharacter, MudRoom, MudExit
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

        u = User(username='parsertest', email='parsertest@example.com', password_hash='x')
        db.session.add(u)
        self.room_a = MudRoom(slug='room-a', zone='test', name='Room A',
                              description='The first room.')
        self.room_b = MudRoom(slug='room-b', zone='test', name='Room B',
                              description='The second room.')
        self.shop_room = MudRoom(slug='room-shop', zone='test', name='Shop Room',
                                 description='A shop.', feature='shop')
        self.healer_room = MudRoom(slug='room-healer', zone='test', name='Healer Room',
                                   description='A healer.', feature='healer')
        db.session.add_all([self.room_a, self.room_b, self.shop_room, self.healer_room])
        db.session.commit()
        db.session.add(MudExit(room_id=self.room_a.id, direction='north',
                               target_room_id=self.room_b.id))
        db.session.commit()
        self.character = MudCharacter(user_id=u.id, name='Tester',
                                      current_room_id=self.room_a.id)
        db.session.add(self.character)
        db.session.commit()

    def _ctx_for(self, session=None):
        from anetbbs.features.mud.parser import GameContext
        return GameContext(session or _FakeSession(), self.character)

    def test_look_shows_room_name_description_and_exits(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'look'))
        txt = session.transcript()
        self.assertIn('Room A', txt)
        self.assertIn('The first room.', txt)
        self.assertIn('north', txt)

    def test_look_abbreviation_l_works_too(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'l'))
        self.assertIn('Room A', session.transcript())

    def test_move_valid_direction_changes_room(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'north'))
        self.assertEqual(self.character.current_room_id, self.room_b.id)
        self.assertIn('Room B', session.transcript())

    def test_move_abbreviation_n_works_too(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'n'))
        self.assertEqual(self.character.current_room_id, self.room_b.id)

    def test_move_invalid_direction_stays_put(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'south'))
        self.assertEqual(self.character.current_room_id, self.room_a.id)
        self.assertIn("can't go south", session.transcript())

    def test_unknown_verb_gives_a_helpful_error(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'xyzzy'))
        self.assertIn("don't understand", session.transcript())

    def test_empty_line_is_a_no_op(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        keep_going = asyncio.run(dispatch(ctx, '   '))
        self.assertTrue(keep_going)
        self.assertEqual(session.transcript(), '')

    def test_quit_ends_the_loop(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        keep_going = asyncio.run(dispatch(ctx, 'quit'))
        self.assertFalse(keep_going)

    def test_q_abbreviation_also_quits(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        keep_going = asyncio.run(dispatch(ctx, 'q'))
        self.assertFalse(keep_going)

    def test_score_shows_character_sheet(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'score'))
        txt = session.transcript()
        self.assertIn('Tester', txt)
        self.assertIn('Level 1', txt)

    def test_help_lists_every_documented_verb(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'help'))
        txt = session.transcript()
        for verb in ('look', 'inventory', 'attack', 'say', 'shop', 'heal', 'quit'):
            self.assertIn(verb, txt)

    def test_say_echoes_with_quotes(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'say hello there'))
        self.assertIn('You say, "hello there"', session.transcript())

    def test_say_with_no_text_prompts_for_it(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'say'))
        self.assertIn('Say what', session.transcript())

    def test_emote_uses_character_name_not_username(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'emote waves'))
        self.assertIn('Tester waves', session.transcript())

    def test_tell_requires_a_target_and_text(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'tell'))
        self.assertIn('Tell whom', session.transcript())

    def test_tell_with_target_and_text_confirms_to_sender(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'tell bob good morning'))
        self.assertIn('You tell bob, "good morning"', session.transcript())

    def test_shop_verb_refused_outside_a_shop_room(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'shop'))
        self.assertIn("no shop here", session.transcript())

    def test_heal_verb_refused_outside_a_healer_room(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'heal'))
        self.assertIn("no healer here", session.transcript())

    def test_shop_verb_works_in_the_shop_room(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.models import db
        self.character.current_room_id = self.shop_room.id
        db.session.commit()
        session = _FakeSession(user={'username': 'parsertest'})
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'shop'))
        self.assertIn('Village Shop', session.transcript())

    def test_inventory_empty_message(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'inventory'))
        self.assertIn("aren't carrying anything", session.transcript())

    def test_equipment_empty_message(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'equipment'))
        self.assertIn('nothing equipped', session.transcript())

    def test_wear_item_not_carried_gives_a_clear_error(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'wear nonexistent sword'))
        self.assertIn("aren't carrying that", session.transcript())

    def test_wear_and_remove_round_trip(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import character as c
        from anetbbs.models import MudItemTemplate, db
        db.session.add(MudItemTemplate(slug='rtsword', name='RT Sword',
                                       item_type='weapon', attack_bonus=2))
        db.session.commit()
        c.add_item(self.character, 'rtsword')
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'wear RT Sword'))
        self.assertIn('equip the RT Sword', session.transcript())
        self.assertTrue(c.find_item_entry(self.character, 'rtsword')['equipped'])
        asyncio.run(dispatch(ctx, 'remove RT Sword'))
        self.assertFalse(c.find_item_entry(self.character, 'rtsword')['equipped'])

    def test_wear_non_equippable_item_is_refused(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import character as c
        from anetbbs.models import MudItemTemplate, db
        db.session.add(MudItemTemplate(slug='potion1', name='Potion One',
                                       item_type='consumable', heal_amount=10))
        db.session.commit()
        c.add_item(self.character, 'potion1')
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'wear Potion One'))
        self.assertIn("can't wear or wield", session.transcript())

    def test_use_consumable_heals_and_is_consumed(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import character as c
        from anetbbs.models import MudItemTemplate, db
        db.session.add(MudItemTemplate(slug='potion2', name='Potion Two',
                                       item_type='consumable', heal_amount=10))
        db.session.commit()
        self.character.hp = 5
        self.character.hp_max = 20
        db.session.commit()
        c.add_item(self.character, 'potion2')
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'use Potion Two'))
        self.assertEqual(self.character.hp, 15)
        self.assertIsNone(c.find_item_entry(self.character, 'potion2'))

    def test_attack_with_no_mob_in_room_is_refused(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'attack'))
        self.assertIn('nothing here to fight', session.transcript())

    def test_who_lists_online_players(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import mp as _mp
        try:
            _mp.publish_presence('parsertest', self.room_a.slug, 0)
            session = _FakeSession()
            ctx = self._ctx_for(session)
            asyncio.run(dispatch(ctx, 'who'))
            self.assertIn('parsertest', session.transcript())
        finally:
            _mp.remove_presence('parsertest')

    def test_score_shows_class_label(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'score'))
        self.assertIn('Tester the Warrior', session.transcript())

    def test_score_shows_mana_only_for_a_mana_using_class(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.models import db
        self.character.char_class = 'mage'
        self.character.mana = 10
        self.character.mana_max = 20
        db.session.commit()
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'score'))
        self.assertIn('Mana: 10/20', session.transcript())

    def test_wear_refuses_an_item_restricted_to_another_class(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import character as c
        from anetbbs.models import MudItemTemplate, db
        db.session.add(MudItemTemplate(slug='mage-staff', name='Mage Staff',
                                       item_type='weapon', attack_bonus=5,
                                       required_class='mage'))
        db.session.commit()
        c.add_item(self.character, 'mage-staff')  # Tester defaults to warrior
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'wear Mage Staff'))
        self.assertIn('Only a Mage can use', session.transcript())
        self.assertFalse(c.find_item_entry(self.character, 'mage-staff')['equipped'])

    def test_wear_allows_an_item_restricted_to_the_characters_own_class(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import character as c
        from anetbbs.models import MudItemTemplate, db
        self.character.char_class = 'mage'
        db.session.add(MudItemTemplate(slug='mage-staff2', name='Mage Staff Two',
                                       item_type='weapon', attack_bonus=5,
                                       required_class='mage'))
        db.session.commit()
        c.add_item(self.character, 'mage-staff2')
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'wear Mage Staff Two'))
        self.assertIn('equip the Mage Staff Two', session.transcript())

    def test_move_regenerates_mana_for_a_mana_using_class(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.models import db
        self.character.char_class = 'mage'
        self.character.mana = 0
        self.character.mana_max = 20
        db.session.commit()
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'north'))
        self.assertGreater(self.character.mana, 0)

    def test_quests_with_none_accepted_says_so(self):
        from anetbbs.features.mud.parser import dispatch
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'quests'))
        self.assertIn("haven't accepted any quests", session.transcript())

    def _make_quest(self, **overrides):
        from anetbbs.models import MudQuestTemplate, db
        kwargs = dict(slug='kill-rats', name='Rat Problem',
                     giver_room_id=self.room_a.id, target_mob_slug='rat',
                     target_count=1, reward_xp=10, reward_gold=5)
        kwargs.update(overrides)
        quest = MudQuestTemplate(**kwargs)
        db.session.add(quest)
        db.session.commit()
        return quest

    def test_look_announces_an_available_quest_in_the_giver_room(self):
        from anetbbs.features.mud.parser import dispatch
        self._make_quest()
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'look'))
        self.assertIn("Rat Problem", session.transcript())
        self.assertIn("accept kill-rats", session.transcript())

    def test_accept_quest_outside_the_giver_room_is_refused(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.models import db
        self._make_quest()
        self.character.current_room_id = self.room_b.id
        db.session.commit()
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'accept kill-rats'))
        self.assertIn("no one here offering", session.transcript())

    def test_accept_then_quests_shows_progress(self):
        from anetbbs.features.mud.parser import dispatch
        self._make_quest()
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'accept kill-rats'))
        self.assertIn('Quest accepted: Rat Problem', session.transcript())
        asyncio.run(dispatch(ctx, 'quests'))
        self.assertIn('Rat Problem -- 0/1', session.transcript())

    def test_turnin_before_completion_is_refused(self):
        from anetbbs.features.mud.parser import dispatch
        self._make_quest()
        session = _FakeSession()
        ctx = self._ctx_for(session)
        asyncio.run(dispatch(ctx, 'accept kill-rats'))
        asyncio.run(dispatch(ctx, 'turnin kill-rats'))
        self.assertIn("isn't ready to turn in", session.transcript())

    def test_turnin_after_completion_grants_reward(self):
        from anetbbs.features.mud.parser import dispatch
        from anetbbs.features.mud import character as c
        self._make_quest()
        c.accept_quest(self.character, 'kill-rats')
        c.record_mob_kill(self.character, 'rat')
        session = _FakeSession()
        ctx = self._ctx_for(session)
        start_gold = self.character.gold
        asyncio.run(dispatch(ctx, 'turnin kill-rats'))
        self.assertIn('Quest complete: Rat Problem', session.transcript())
        self.assertEqual(self.character.gold, start_gold + 5)


if __name__ == '__main__':
    unittest.main()
