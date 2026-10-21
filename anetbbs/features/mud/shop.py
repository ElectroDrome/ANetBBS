# anetbbs/features/mud/shop.py
"""The village shop (buy/sell real MudItemTemplate stock) and healer
NPC (pay gold to fully restore HP). Buy/sell-gold-check pattern
adapted from data/mods/doors/robot_rpg/shop.py's own real precedent
(`if player['gold'] < price: refuse`), extended to a real item catalog
-- robot_rpg's own shop only had two fixed upgrade tracks (weapon/
armor tier), not a browsable multi-item list, since this one's catalog
is admin-editable (Admin -> MUD -> Items) rather than hardcoded.
"""
from . import character as _char

SELL_FRACTION = 0.4  # sell-back price = floor(value * 0.4), never less than 1
HEAL_COST_PER_HP = 2
HEAL_COST_PER_MANA = 1
# Each item takes 2 display lines (name+price, then its description) --
# 6 fits comfortably under a fixed 80x25 terminal alongside the header/
# gold line/prompt/page-nav chrome, with room to spare for a 2-line
# wrapped description. Real bug reported live: with no pagination at
# all, a catalog of a dozen-plus items ran off the top of the screen
# before the player could see the header or the cheapest items --
# same shape bug _show_category_submenu() already had to solve for a
# 100+-game door category.
ITEMS_PER_PAGE = 6


async def run_shop(session, character):
    from ...models import MudItemTemplate
    stock = MudItemTemplate.query.filter_by(is_shop_stock=True).order_by(
        MudItemTemplate.value).all()
    page = 0
    total_pages = max(1, (len(stock) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    while True:
        page = max(0, min(page, total_pages - 1))
        start = page * ITEMS_PER_PAGE
        end = min(start + ITEMS_PER_PAGE, len(stock))
        if hasattr(session, 'clear_screen'):
            await session.clear_screen()
        await session.write("\r\n\x1b[1;36m-- Village Shop --\x1b[0m\r\n"
                            f"Your gold: {character.gold}\r\n\r\n")
        for i in range(start, end):
            item = stock[i]
            desc_lines = '\r\n'.join(_char.word_wrap(item.description or '', indent='     '))
            await session.write(f"  {i + 1}. {item.name} -- {item.value} gold\r\n"
                                f"     {desc_lines}\r\n")
        if total_pages > 1:
            await session.write(
                f"\r\n-- page {page + 1}/{total_pages} "
                f"({start + 1}-{end} of {len(stock)}) --\r\n")
        nav = "[B]uy <number>  [S]ell <item>  [L]ist inventory"
        if total_pages > 1:
            if page > 0:
                nav += "  [P]rev page"
            if page < total_pages - 1:
                nav += "  [N]ext page"
        await session.write(f"\r\n{nav}  [Q]uit: ")
        choice = (await session.read_line('') or '').strip()
        if not choice or choice.lower() in ('q', 'quit'):
            return
        low = choice.lower()
        if low == 'n' and page < total_pages - 1:
            page += 1
        elif low == 'p' and page > 0:
            page -= 1
        elif low in ('l', 'list'):
            await _print_inventory(session, character)
        elif low.startswith('b'):
            await _handle_buy(session, character, stock, choice)
        elif low.startswith('s'):
            await _handle_sell(session, character, choice)
        else:
            await session.write("\r\nBuy, sell, list, or quit.\r\n")


async def _print_inventory(session, character):
    from ...models import MudItemTemplate
    inv = _char.inventory(character)
    if not inv:
        await session.write("\r\nYou aren't carrying anything.\r\n")
        return
    await session.write("\r\nYour inventory:\r\n")
    for entry in inv:
        item = MudItemTemplate.query.filter_by(slug=entry['item_slug']).first()
        name = item.name if item else entry['item_slug']
        tag = ' (equipped)' if entry.get('equipped') else ''
        await session.write(f"  {name} x{entry.get('qty', 1)}{tag}\r\n")


async def _handle_buy(session, character, stock, choice):
    from ...models import db
    parts = choice.split(None, 1)
    if len(parts) < 2:
        await session.write("\r\nBuy which number?\r\n")
        return
    try:
        idx = int(parts[1].strip()) - 1
    except ValueError:
        await session.write("\r\nBuy which number?\r\n")
        return
    if not (0 <= idx < len(stock)):
        await session.write("\r\nNo such item.\r\n")
        return
    item = stock[idx]
    if character.gold < item.value:
        await session.write(f"\r\nYou can't afford the {item.name} "
                            f"({item.value} gold).\r\n")
        return
    character.gold -= item.value
    db.session.commit()
    _char.add_item(character, item.slug)
    await session.write(f"\r\nYou bought a {item.name}.\r\n")


async def _handle_sell(session, character, choice):
    from ...models import db, MudItemTemplate
    parts = choice.split(None, 1)
    if len(parts) < 2:
        await session.write("\r\nSell which item?\r\n")
        return
    name_or_slug = parts[1].strip().lower()
    inv = _char.inventory(character)
    entry = None
    item = None
    for e in inv:
        candidate = MudItemTemplate.query.filter_by(slug=e['item_slug']).first()
        if candidate and (candidate.slug.lower() == name_or_slug
                          or candidate.name.lower() == name_or_slug):
            entry, item = e, candidate
            break
    if entry is None:
        await session.write("\r\nYou don't have that.\r\n")
        return
    price = max(1, int(item.value * SELL_FRACTION))
    _char.remove_item(character, item.slug, 1)
    character.gold += price
    db.session.commit()
    await session.write(f"\r\nYou sold the {item.name} for {price} gold.\r\n")


async def run_healer(session, character):
    from ...models import db
    missing_hp = character.hp_max - character.hp
    missing_mana = max(0, character.mana_max - character.mana)
    if missing_hp <= 0 and missing_mana <= 0:
        await session.write("\r\nThe healer smiles. \"You're already at full health.\"\r\n")
        return
    cost = missing_hp * HEAL_COST_PER_HP + missing_mana * HEAL_COST_PER_MANA
    mana_note = f" and {missing_mana} mana" if missing_mana else ''
    await session.write(
        f"\r\nThe healer can restore {missing_hp} HP{mana_note} for {cost} gold. "
        f"Heal? [y/N]: ")
    resp = (await session.read_line('') or '').strip().lower()
    if resp != 'y':
        await session.write("\r\nMaybe another time.\r\n")
        return
    if character.gold < cost:
        await session.write("\r\nYou don't have enough gold.\r\n")
        return
    character.gold -= cost
    character.hp = character.hp_max
    character.mana = character.mana_max
    db.session.commit()
    await session.write("\r\nYou feel completely restored!\r\n")
