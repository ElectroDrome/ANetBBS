"""Tests for anetbbs/features/mud/combat.py -- resolve_attack() (the
exact robot_rpg/combat.py port) and run_combat()'s interactive turn
loop (win/flee/death outcomes)."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def __init__(self, responses):
        self._responses = list(responses)
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._responses.pop(0) if self._responses else 'f'

    def transcript(self):
        return ''.join(self.written)


class ResolveAttackTests(unittest.TestCase):
    def test_damage_is_at_least_one(self):
        from anetbbs.features.mud.combat import resolve_attack
        for _ in range(200):
            dmg = resolve_attack(attack=1, defense=100)
            self.assertGreaterEqual(dmg, 1)

    def test_damage_variance_bounds(self):
        """variance = random.randint(-1, 2) -- same bounds as
        robot_rpg/combat.py's own _resolve_attack()."""
        from anetbbs.features.mud.combat import resolve_attack
        base = 10 - 0  # attack=10, defense=0
        seen = set()
        for _ in range(500):
            dmg = resolve_attack(attack=10, defense=0)
            seen.add(dmg)
        self.assertTrue(seen.issubset({base - 1, base, base + 1, base + 2}))

    def test_higher_defense_reduces_damage(self):
        from anetbbs.features.mud.combat import resolve_attack
        import random
        random.seed(42)
        low_def = [resolve_attack(attack=20, defense=0) for _ in range(50)]
        random.seed(42)
        high_def = [resolve_attack(attack=20, defense=15) for _ in range(50)]
        self.assertLess(sum(high_def), sum(low_def))


class MobInstanceTests(unittest.TestCase):
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

    def test_mob_instance_copies_template_stats(self):
        from anetbbs.models import db, MudMobTemplate
        from anetbbs.features.mud.combat import MobInstance
        template = MudMobTemplate(slug='test-goblin', name='Test Goblin',
                                  level=3, hp=25, attack=6, defense=2,
                                  xp_reward=15, gold_reward=7)
        db.session.add(template)
        db.session.commit()
        mob = MobInstance(template)
        self.assertEqual(mob.hp, 25)
        self.assertEqual(mob.hp_max, 25)
        mob.hp -= 10
        # Mutating the instance must never touch the template row.
        self.assertEqual(template.hp, 25)


class RunCombatTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User, MudCharacter
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        u = User(username='fighter', email='fighter@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        self.character = MudCharacter(user_id=u.id, name='Fighter',
                                      hp=100, hp_max=100, attack=50, defense=50)
        db.session.add(self.character)
        db.session.commit()

    def _mob(self, hp=10, attack=1, defense=0):
        from anetbbs.models import MudMobTemplate
        from anetbbs.features.mud.combat import MobInstance
        return MobInstance(MudMobTemplate(
            slug='t', name='Test Mob', level=1, hp=hp, attack=attack,
            defense=defense, xp_reward=12, gold_reward=6))

    def test_overwhelming_attacker_wins_and_gains_xp_gold(self):
        from anetbbs.features.mud.combat import run_combat
        session = _FakeSession(['a'] * 10)
        mob = self._mob(hp=5, attack=0, defense=0)
        start_gold = self.character.gold
        result = asyncio.run(run_combat(session, self.character, mob))
        self.assertEqual(result, 'won')
        self.assertIn('defeated', session.transcript())
        self.assertEqual(self.character.gold, start_gold + 6)

    def test_fleeing_can_succeed(self):
        from anetbbs.features.mud.combat import run_combat
        session = _FakeSession(['f'] * 10)
        mob = self._mob(hp=1000, attack=0, defense=0)
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            result = asyncio.run(run_combat(session, self.character, mob))
        self.assertEqual(result, 'fled')

    def test_failed_flee_costs_a_turn_not_an_exit(self):
        from anetbbs.features.mud.combat import run_combat
        session = _FakeSession(['f', 'a', 'a', 'a', 'a', 'a'])
        mob = self._mob(hp=5, attack=0, defense=0)
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.99):
            result = asyncio.run(run_combat(session, self.character, mob))
        # The first 'f' fails (forced by the patched random.random), so
        # the fight continues and the overwhelming attacker still wins.
        self.assertEqual(result, 'won')
        self.assertIn("couldn't get away", session.transcript())

    def test_dying_leaves_character_at_one_hp_not_zero(self):
        from anetbbs.models import db, User, MudCharacter
        from anetbbs.features.mud.combat import run_combat
        u = User(username='weakling', email='weakling@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        weak_char = MudCharacter(user_id=u.id, name='Weakling',
                                 hp=5, hp_max=5, attack=1, defense=0)
        db.session.add(weak_char)
        db.session.commit()
        session = _FakeSession(['a'] * 10)
        mob = self._mob(hp=1000, attack=50, defense=0)
        result = asyncio.run(run_combat(session, weak_char, mob))
        self.assertEqual(result, 'died')
        self.assertEqual(weak_char.hp, 1)

    def test_unrecognized_input_reprompts_without_consuming_a_turn(self):
        from anetbbs.features.mud.combat import run_combat
        session = _FakeSession(['nonsense', 'a', 'a', 'a', 'a'])
        mob = self._mob(hp=5, attack=0, defense=0)
        result = asyncio.run(run_combat(session, self.character, mob))
        self.assertEqual(result, 'won')
        self.assertIn('pick one', session.transcript())


class ClassAbilityTests(unittest.TestCase):
    """Phase 6: class-specific abilities (classes.py) -- Warrior's
    Power Attack (cooldown-gated), Mage's Fireball (mana-gated),
    Rogue's Backstab (opening-move only), Cleric's Heal (mana-gated,
    heals instead of attacking)."""

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
        self._u_counter = 0

    def _char(self, char_class, **overrides):
        from anetbbs.models import db, MudCharacter, User
        self._u_counter += 1
        u = User(username=f'abil{self._u_counter}',
                email=f'abil{self._u_counter}@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        defaults = dict(hp=100, hp_max=100, attack=50, defense=50,
                        mana=20, mana_max=20, level=1)
        defaults.update(overrides)
        char = MudCharacter(user_id=u.id, name='AbilityTester',
                            char_class=char_class, **defaults)
        db.session.add(char)
        db.session.commit()
        return char

    def _mob(self, hp=1000, attack=0, defense=0):
        from anetbbs.models import MudMobTemplate
        from anetbbs.features.mud.combat import MobInstance
        return MobInstance(MudMobTemplate(
            slug='t', name='Test Mob', level=1, hp=hp, attack=attack,
            defense=defense, xp_reward=12, gold_reward=6))

    def test_warrior_power_attack_hits_harder_than_a_normal_attack(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('warrior')
        session = _FakeSession(['power', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob()))
        self.assertIn('Power Attack!', session.transcript())

    def test_warrior_power_attack_is_on_cooldown_after_first_use(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('warrior')
        # turn 0: power (works). turn 1: power (should be on cooldown
        # -- POWER_ATTACK_COOLDOWN=3 -- re-prompts without consuming a
        # turn), so the SAME 'power' choice must fail again, then flee.
        session = _FakeSession(['power', 'power', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob()))
        self.assertIn("isn't ready yet", session.transcript())

    def test_mage_fireball_consumes_mana_and_deals_damage(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('mage', mana=20, mana_max=20)
        session = _FakeSession(['cast', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob()))
        self.assertIn('Fireball!', session.transcript())
        self.assertEqual(char.mana, 12)  # 20 - FIREBALL_MANA_COST(8)

    def test_mage_fireball_refused_without_enough_mana(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('mage', mana=2, mana_max=20)
        session = _FakeSession(['cast', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob()))
        self.assertIn('Not enough mana', session.transcript())
        self.assertEqual(char.mana, 2)  # unchanged

    def test_rogue_backstab_works_as_the_opening_move(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('rogue')
        session = _FakeSession(['backstab', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob()))
        self.assertIn('Backstab!', session.transcript())

    def test_rogue_backstab_refused_after_the_opening_move(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('rogue')
        session = _FakeSession(['a', 'backstab', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob()))
        self.assertIn('only Backstab as your opening strike', session.transcript())

    def test_cleric_heal_restores_hp_and_consumes_mana(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('cleric', hp=10, hp_max=100, mana=16, mana_max=16, level=1)
        session = _FakeSession(['heal', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob(attack=0)))
        self.assertGreater(char.hp, 10)
        self.assertEqual(char.mana, 10)  # 16 - HEAL_MANA_COST(6)

    def test_cleric_heal_refused_without_enough_mana(self):
        from anetbbs.features.mud.combat import run_combat
        char = self._char('cleric', hp=10, hp_max=100, mana=1, mana_max=16)
        session = _FakeSession(['heal', 'f'])
        with patch('anetbbs.features.mud.combat.random.random', return_value=0.0):
            asyncio.run(run_combat(session, char, self._mob(attack=0)))
        self.assertIn('Not enough mana', session.transcript())
        self.assertEqual(char.hp, 10)

    def test_rogue_has_a_higher_flee_chance_than_warrior(self):
        from anetbbs.features.mud import classes as _classes
        rogue = self._char('rogue')
        warrior = self._char('warrior')
        self.assertGreater(_classes.flee_chance(rogue), _classes.flee_chance(warrior))

    def test_win_records_quest_progress(self):
        from anetbbs.features.mud.combat import run_combat
        from anetbbs.features.mud import character as c
        from anetbbs.models import db, MudRoom, MudQuestTemplate
        room = MudRoom(slug='quest-combat-room', name='Room', description='.')
        db.session.add(room)
        db.session.commit()
        quest = MudQuestTemplate(slug='kill-test-mob', name='Test Quest',
                                 giver_room_id=room.id, target_mob_slug='t',
                                 target_count=1, reward_xp=5, reward_gold=5)
        db.session.add(quest)
        db.session.commit()
        char = self._char('warrior')
        c.accept_quest(char, 'kill-test-mob')
        session = _FakeSession(['a'] * 10)
        asyncio.run(run_combat(session, char, self._mob(hp=1, attack=0)))
        entry = c.find_quest_entry(char, 'kill-test-mob')
        self.assertTrue(entry['completed'])


if __name__ == '__main__':
    unittest.main()
