"""Tests for anetbbs/features/mud/shop.py -- the village shop buy/sell
and healer, adapted from robot_rpg/shop.py's own real gold-check
precedent, extended to a real multi-item catalog."""
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
    def __init__(self, responses):
        self._responses = list(responses)
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._responses.pop(0) if self._responses else 'q'

    def transcript(self):
        return ''.join(self.written)


class MudShopTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User, MudCharacter, MudItemTemplate
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        db.session.add(MudItemTemplate(
            slug='shop-sword', name='Shop Sword', item_type='weapon',
            attack_bonus=3, value=20, is_shop_stock=True))
        db.session.add(MudItemTemplate(
            slug='not-for-sale', name='Secret Item', item_type='quest',
            value=1000, is_shop_stock=False))
        u = User(username='shopper', email='shopper@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        self.character = MudCharacter(user_id=u.id, name='Shopper', gold=50)
        db.session.add(self.character)
        db.session.commit()

    def test_only_shop_stock_items_are_listed(self):
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['q'])
        asyncio.run(run_shop(session, self.character))
        txt = session.transcript()
        self.assertIn('Shop Sword', txt)
        self.assertNotIn('Secret Item', txt)

    def test_buy_deducts_gold_and_adds_item(self):
        """create_app('testing') also seeds the real Shattered Realm
        world (anetbbs/features/mud/seed.py), so the shop's real stock
        isn't just this test's own 'shop-sword' row -- find its actual
        1-based position in the real combined, value-ordered list
        rather than assuming it's item 1."""
        from anetbbs.features.mud.shop import run_shop
        from anetbbs.features.mud import character as c
        from anetbbs.models import MudItemTemplate
        stock = MudItemTemplate.query.filter_by(is_shop_stock=True).order_by(
            MudItemTemplate.value).all()
        idx = next(i for i, item in enumerate(stock, start=1)
                  if item.slug == 'shop-sword')
        session = _FakeSession([f'b {idx}', 'q'])
        start_gold = self.character.gold
        asyncio.run(run_shop(session, self.character))
        self.assertEqual(self.character.gold, start_gold - 20)
        self.assertIsNotNone(c.find_item_entry(self.character, 'shop-sword'))

    def test_buy_insufficient_gold_is_refused(self):
        from anetbbs.features.mud.shop import run_shop
        from anetbbs.models import db
        self.character.gold = 5
        db.session.commit()
        session = _FakeSession(['b 1', 'q'])
        asyncio.run(run_shop(session, self.character))
        self.assertEqual(self.character.gold, 5)
        self.assertIn("can't afford", session.transcript())

    def test_buy_invalid_number_is_rejected_cleanly(self):
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['b 99', 'q'])
        asyncio.run(run_shop(session, self.character))
        self.assertIn('No such item', session.transcript())

    def test_sell_returns_a_fraction_of_value(self):
        from anetbbs.features.mud.shop import run_shop
        from anetbbs.features.mud import character as c
        c.add_item(self.character, 'shop-sword')
        session = _FakeSession(['s Shop Sword', 'q'])
        start_gold = self.character.gold
        asyncio.run(run_shop(session, self.character))
        # value=20, SELL_FRACTION=0.4 -> 8 gold
        self.assertEqual(self.character.gold, start_gold + 8)
        self.assertIsNone(c.find_item_entry(self.character, 'shop-sword'))

    def test_sell_item_not_carried_is_refused(self):
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['s Shop Sword', 'q'])
        asyncio.run(run_shop(session, self.character))
        self.assertIn("don't have that", session.transcript())

    def test_list_inventory_shows_equipped_tag(self):
        from anetbbs.features.mud.shop import run_shop
        from anetbbs.features.mud import character as c
        c.add_item(self.character, 'shop-sword')
        c.set_equipped(self.character, 'shop-sword', True)
        session = _FakeSession(['l', 'q'])
        asyncio.run(run_shop(session, self.character))
        self.assertIn('(equipped)', session.transcript())


class MudHealerTests(unittest.TestCase):
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
        u = User(username='patient', email='patient@example.com', password_hash='x')
        db.session.add(u)
        db.session.commit()
        self.character = MudCharacter(user_id=u.id, name='Patient',
                                      hp=5, hp_max=20, gold=100)
        db.session.add(self.character)
        db.session.commit()

    def test_already_full_health_needs_no_payment(self):
        from anetbbs.features.mud.shop import run_healer
        from anetbbs.models import db
        self.character.hp = 20
        db.session.commit()
        session = _FakeSession([])
        asyncio.run(run_healer(session, self.character))
        self.assertIn('already at full health', session.transcript())

    def test_declining_leaves_gold_and_hp_unchanged(self):
        from anetbbs.features.mud.shop import run_healer
        session = _FakeSession(['n'])
        asyncio.run(run_healer(session, self.character))
        self.assertEqual(self.character.hp, 5)
        self.assertEqual(self.character.gold, 100)

    def test_accepting_heals_and_charges_the_right_amount(self):
        from anetbbs.features.mud.shop import run_healer
        session = _FakeSession(['y'])
        asyncio.run(run_healer(session, self.character))
        # missing=15, HEAL_COST_PER_HP=2 -> 30 gold
        self.assertEqual(self.character.hp, 20)
        self.assertEqual(self.character.gold, 70)

    def test_accepting_without_enough_gold_is_refused(self):
        from anetbbs.models import db
        from anetbbs.features.mud.shop import run_healer
        self.character.gold = 5
        db.session.commit()
        session = _FakeSession(['y'])
        asyncio.run(run_healer(session, self.character))
        self.assertEqual(self.character.hp, 5)
        self.assertIn("don't have enough gold", session.transcript())


class MudShopPaginationTests(unittest.TestCase):
    """Real bug reported live (screenshot): with no pagination at all,
    a shop catalog longer than ITEMS_PER_PAGE ran the header and
    cheapest items off the top of the screen before the player could
    ever see them. Uses a fresh, isolated DB (not the shared setUp
    above) with a known, controlled item count so page boundaries are
    deterministic rather than depending on the real seeded world's own
    item count."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User, MudCharacter, MudItemTemplate
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        # 9 items, 2 more than ITEMS_PER_PAGE (6) -- guarantees a real
        # second page regardless of what the seeded world also adds.
        from anetbbs.features.mud.shop import ITEMS_PER_PAGE
        self.items_per_page = ITEMS_PER_PAGE
        for i in range(1, 10):
            db.session.add(MudItemTemplate(
                slug=f'page-item-{i}', name=f'Page Item {i}',
                item_type='misc', value=i, is_shop_stock=True))
        u = User(username='pageshopper', email='pageshopper@example.com',
                 password_hash='x')
        db.session.add(u)
        db.session.commit()
        self.character = MudCharacter(user_id=u.id, name='PageShopper', gold=50)
        db.session.add(self.character)
        db.session.commit()

    def test_first_page_shows_only_the_cheapest_items(self):
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['q'])
        asyncio.run(run_shop(session, self.character))
        txt = session.transcript()
        self.assertIn('Page Item 1', txt)
        # The last (most expensive, real seed world included) item must
        # NOT be on page 1 -- real pagination, not just a page indicator
        # slapped on an already-complete listing.
        self.assertNotIn('Page Item 9', txt)

    def test_page_indicator_shown_when_more_than_one_page(self):
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['q'])
        asyncio.run(run_shop(session, self.character))
        self.assertIn('page 1/', session.transcript())

    def test_next_page_reveals_later_items(self):
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['n', 'q'])
        asyncio.run(run_shop(session, self.character))
        txt = session.transcript()
        self.assertIn('Page Item 9', txt)

    def test_buy_by_absolute_number_works_from_a_later_page(self):
        """Numbering stays absolute across pages -- matches
        _show_category_submenu()'s own established convention."""
        from anetbbs.features.mud.shop import run_shop
        from anetbbs.features.mud import character as c
        session = _FakeSession(['n', 'b 9', 'q'])
        asyncio.run(run_shop(session, self.character))
        self.assertIsNotNone(c.find_item_entry(self.character, 'page-item-9'))

    def test_single_page_shows_no_page_indicator(self):
        """create_app('testing') also seeds the real Shattered Realm
        world's own shop stock (same caveat as test_buy_deducts_gold_
        and_adds_item above) -- clear ALL shop stock (seeded and this
        test's own) down to exactly one controlled item so the page
        count is deterministic."""
        from anetbbs.models import db, MudItemTemplate
        MudItemTemplate.query.update({'is_shop_stock': False})
        db.session.add(MudItemTemplate(
            slug='page-item-solo', name='Solo Item', item_type='misc',
            value=5, is_shop_stock=True))
        db.session.commit()
        from anetbbs.features.mud.shop import run_shop
        session = _FakeSession(['q'])
        asyncio.run(run_shop(session, self.character))
        self.assertNotIn('page 1/', session.transcript())


if __name__ == '__main__':
    unittest.main()
