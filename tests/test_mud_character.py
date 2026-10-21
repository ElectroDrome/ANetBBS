"""Tests for anetbbs/features/mud/character.py -- inventory (add/
remove/equip), effective combat stats, and leveling (the robot_rpg-
ported XP curve)."""
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


class MudCharacterHelpersTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User, MudItemTemplate
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        u = User(username='chartest', email='chartest@example.com', password_hash='x')
        db.session.add(u)
        db.session.add(MudItemTemplate(
            slug='test-sword', name='Test Sword', item_type='weapon',
            attack_bonus=5))
        db.session.add(MudItemTemplate(
            slug='test-shield', name='Test Shield', item_type='armor',
            defense_bonus=3))
        db.session.add(MudItemTemplate(
            slug='test-potion', name='Test Potion', item_type='consumable',
            heal_amount=15))
        db.session.commit()
        self.user_id = u.id

    def _make_character(self):
        from anetbbs.models import db, MudCharacter
        char = MudCharacter(user_id=self.user_id, name='Hero')
        db.session.add(char)
        db.session.commit()
        return char

    def test_add_item_new(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        c.add_item(char, 'test-sword')
        inv = c.inventory(char)
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]['item_slug'], 'test-sword')
        self.assertEqual(inv[0]['qty'], 1)
        self.assertFalse(inv[0]['equipped'])

    def test_add_item_stacks_quantity(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        c.add_item(char, 'test-potion', 2)
        c.add_item(char, 'test-potion', 3)
        inv = c.inventory(char)
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]['qty'], 5)

    def test_remove_item_insufficient_quantity_fails_cleanly(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        c.add_item(char, 'test-potion', 1)
        self.assertFalse(c.remove_item(char, 'test-potion', 5))
        self.assertEqual(c.inventory(char)[0]['qty'], 1)

    def test_remove_item_removes_entry_entirely_at_zero(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        c.add_item(char, 'test-potion', 1)
        self.assertTrue(c.remove_item(char, 'test-potion', 1))
        self.assertEqual(c.inventory(char), [])

    def test_set_equipped_unknown_item_returns_false(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        self.assertFalse(c.set_equipped(char, 'nope', True))

    def test_effective_attack_includes_equipped_weapon_bonus(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        base = c.effective_attack(char)
        c.add_item(char, 'test-sword')
        c.set_equipped(char, 'test-sword', True)
        self.assertEqual(c.effective_attack(char), base + 5)

    def test_unequipped_item_does_not_count_toward_effective_stats(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        base = c.effective_attack(char)
        c.add_item(char, 'test-sword')  # not equipped
        self.assertEqual(c.effective_attack(char), base)

    def test_effective_defense_includes_equipped_armor_bonus(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        base = c.effective_defense(char)
        c.add_item(char, 'test-shield')
        c.set_equipped(char, 'test-shield', True)
        self.assertEqual(c.effective_defense(char), base + 3)

    def test_gain_xp_no_level_up_below_threshold(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        self.assertEqual(char.xp_next, 20)
        c.gain_xp(char, 5)
        self.assertEqual(char.xp, 5)
        self.assertEqual(char.level, 1)

    def test_gain_xp_single_level_up_matches_robot_rpg_curve(self):
        """XP side ported verbatim from data/mods/doors/robot_rpg/
        player.py's own gain_xp(): xp_next *= 1.5, full heal, on every
        level gained. Stat gains are the default Warrior class's own
        growth (classes.py's CLASSES['warrior']): +7 hp_max, +2
        attack, +1 defense per level."""
        from anetbbs.features.mud import character as c
        char = self._make_character()
        base_hp_max, base_attack, base_defense = char.hp_max, char.attack, char.defense
        char.hp = 1  # confirm level-up fully heals
        from anetbbs.models import db
        db.session.commit()
        c.gain_xp(char, 20)  # exactly xp_next
        self.assertEqual(char.level, 2)
        self.assertEqual(char.xp, 0)
        self.assertEqual(char.xp_next, 30)  # int(20 * 1.5)
        self.assertEqual(char.hp_max, base_hp_max + 7)
        self.assertEqual(char.hp, char.hp_max)
        self.assertEqual(char.attack, base_attack + 2)
        self.assertEqual(char.defense, base_defense + 1)

    def test_gain_xp_multiple_level_ups_in_one_award(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        c.gain_xp(char, 20 + 30)  # exactly enough for 2 level-ups
        self.assertEqual(char.level, 3)
        self.assertEqual(char.xp, 0)

    def test_gain_xp_calls_on_level_up_once_per_level(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        levels_seen = []
        c.gain_xp(char, 20 + 30, on_level_up=levels_seen.append)
        self.assertEqual(levels_seen, [2, 3])

    def test_get_or_create_character_returns_none_when_absent(self):
        from anetbbs.features.mud import character as c
        from anetbbs.models import db, User
        u2 = User(username='nocharyet', email='nocharyet@example.com', password_hash='x')
        db.session.add(u2)
        db.session.commit()
        self.assertIsNone(c.get_or_create_character(u2.id))

    def test_inventory_handles_corrupted_json_gracefully(self):
        from anetbbs.features.mud import character as c
        char = self._make_character()
        char.inventory_json = 'not valid json{{{'
        self.assertEqual(c.inventory(char), [])


class WordWrapTests(unittest.TestCase):
    """Regression tests for character.word_wrap() -- real bug reported
    live (screenshots): class descriptions, room descriptions, and shop
    item descriptions were each written as a single unwrapped line and
    left to the terminal's own hardware auto-wrap, which breaks
    mid-word with zero word-boundary awareness ("...straight fight, n"
    / "o spells." -- "no" split across the hardware wrap). See
    word_wrap()'s own docstring for the full root-cause writeup and why
    width defaults to 79, never 80."""

    def test_no_line_exceeds_the_width(self):
        from anetbbs.features.mud.character import word_wrap
        text = ('A battle-hardened fighter -- high HP, strong in a '
                'straight fight, no spells.')
        for line in word_wrap(text, width=79):
            self.assertLessEqual(len(line), 79)

    def test_never_splits_a_word_mid_token(self):
        """The exact reported bug: "no" must never be split into "n"
        on one line and "o" starting the next."""
        from anetbbs.features.mud.character import word_wrap
        text = ('A battle-hardened fighter -- high HP, strong in a '
                'straight fight, no spells.')
        lines = word_wrap(text, width=79)
        rejoined_words = ' '.join(lines).split()
        self.assertIn('no', rejoined_words)
        self.assertNotIn('n', rejoined_words)

    def test_wraps_when_text_exceeds_width(self):
        from anetbbs.features.mud.character import word_wrap
        text = 'word ' * 30  # far longer than 79 columns
        lines = word_wrap(text, width=79)
        self.assertGreater(len(lines), 1)

    def test_short_text_is_not_wrapped(self):
        from anetbbs.features.mud.character import word_wrap
        lines = word_wrap('A short room.', width=79)
        self.assertEqual(lines, ['A short room.'])

    def test_embedded_newline_is_a_hard_break_not_reflowable_whitespace(self):
        from anetbbs.features.mud.character import word_wrap
        lines = word_wrap('First paragraph.\nSecond paragraph.', width=79)
        self.assertEqual(lines, ['First paragraph.', 'Second paragraph.'])

    def test_subsequent_lines_get_the_hanging_indent(self):
        from anetbbs.features.mud.character import word_wrap
        text = 'word ' * 30
        lines = word_wrap(text, width=20, indent='  ')
        for line in lines[1:]:
            self.assertTrue(line.startswith('  '))

    def test_default_width_is_79_not_80(self):
        from anetbbs.features.mud.character import MUD_TEXT_WIDTH
        self.assertEqual(MUD_TEXT_WIDTH, 79)


if __name__ == '__main__':
    unittest.main()
