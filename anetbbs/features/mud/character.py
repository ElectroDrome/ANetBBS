# anetbbs/features/mud/character.py
"""MudCharacter helpers: load/create, inventory (parse/serialize the
compact inventory_json list), equip/unequip, effective combat stats
(base + equipped item bonuses), leveling, and quest progress.

Leveling curve's XP side is ported verbatim from data/mods/doors/
robot_rpg/player.py's own gain_xp() (xp_next *= 1.5 per level, full
heal on level-up); the per-level STAT gains are class-specific (see
classes.py) rather than robot_rpg's one flat curve, since a Warrior
and a Mage should not grow identically.
"""
import json
import textwrap

from . import classes as _classes

MUD_TEXT_WIDTH = 79  # never the full 80 -- see word_wrap()'s own docstring


def word_wrap(text, width=MUD_TEXT_WIDTH, indent=''):
    """Word-wrap *text* to *width* columns, one physical line per list
    entry. Real bug found live: class descriptions (classes.py),
    room descriptions, and shop item descriptions were all written as
    a single unwrapped line each and left to the terminal's own
    hardware auto-wrap, which breaks mid-word with no word-boundary
    awareness (e.g. "...straight fight, n" / "o spells." -- "no" split
    across the wrap) -- same bug class and same fix shape as
    anetbbs/core/session.py's own _wrap_text_lines() (PETSCII
    security-question prompts, v1.0.26).

    width defaults to 79, not 80: this subprocess's PTY is always
    opened at a fixed 80x25 (door_runner.py's own TIOCSWINSZ call --
    this launch path never gets a real NAWS negotiation to learn an
    actual terminal width), and writing all the way into the last
    column risks an ambiguous double-wrap on terminals that autowrap
    immediately at column N rather than deferring to the next
    printable character (confirmed real behavior difference between
    xterm-family and SyncTerm).

    A literal '\\n' in *text* is a hard paragraph break, not reflowable
    whitespace -- split first, wrap each segment independently, same
    reasoning as the v1.0.27 embedded-newline fix."""
    out = []
    for para in (text or '').split('\n'):
        out.extend(textwrap.wrap(para, width=max(10, width),
                                 subsequent_indent=indent) or [''])
    return out


def get_or_create_character(user_id, default_room_id=None):
    """Returns the user's MudCharacter, or None if they haven't created
    one yet (first-play character creation is handled by the caller,
    since it needs interactive input this helper doesn't have)."""
    from ...models import MudCharacter
    return MudCharacter.query.filter_by(user_id=user_id).first()


def create_character(user_id, name, starting_room_id, char_class='warrior'):
    from ...models import db, MudCharacter
    char = MudCharacter(user_id=user_id, name=name,
                        current_room_id=starting_room_id)
    _classes.apply_class_base_stats(char, char_class)
    db.session.add(char)
    db.session.commit()
    return char


def inventory(character):
    """Parsed inventory_json -> list of dicts
    {'item_slug':str,'qty':int,'equipped':bool}. Never raises -- a
    corrupted/empty column just reads back as an empty inventory."""
    try:
        data = json.loads(character.inventory_json or '[]')
        if isinstance(data, list):
            return data
    except (ValueError, TypeError):
        pass
    return []


def _save_inventory(character, inv):
    from ...models import db
    character.inventory_json = json.dumps(inv)
    db.session.commit()


def find_item_entry(character, item_slug):
    for entry in inventory(character):
        if entry.get('item_slug') == item_slug:
            return entry
    return None


def add_item(character, item_slug, qty=1):
    inv = inventory(character)
    entry = next((e for e in inv if e.get('item_slug') == item_slug), None)
    if entry is not None:
        entry['qty'] = entry.get('qty', 0) + qty
    else:
        inv.append({'item_slug': item_slug, 'qty': qty, 'equipped': False})
    _save_inventory(character, inv)


def remove_item(character, item_slug, qty=1):
    """Returns True if `qty` was actually removed, False if the
    character didn't have enough (nothing is changed in that case)."""
    inv = inventory(character)
    entry = next((e for e in inv if e.get('item_slug') == item_slug), None)
    if entry is None or entry.get('qty', 0) < qty:
        return False
    entry['qty'] -= qty
    if entry['qty'] <= 0:
        inv.remove(entry)
    _save_inventory(character, inv)
    return True


def set_equipped(character, item_slug, equipped):
    """Returns True if the item was found and updated, False if the
    character doesn't carry it."""
    inv = inventory(character)
    entry = next((e for e in inv if e.get('item_slug') == item_slug), None)
    if entry is None:
        return False
    entry['equipped'] = equipped
    _save_inventory(character, inv)
    return True


def equipped_items(character):
    """MudItemTemplate rows currently equipped, in no particular
    order."""
    from ...models import MudItemTemplate
    slugs = [e['item_slug'] for e in inventory(character) if e.get('equipped')]
    if not slugs:
        return []
    return MudItemTemplate.query.filter(MudItemTemplate.slug.in_(slugs)).all()


def can_equip(character, item):
    """False if `item.required_class` is set and doesn't match this
    character's own class -- None/blank means any class can use it."""
    return not item.required_class or item.required_class == character.char_class


def effective_attack(character):
    return character.attack + sum(i.attack_bonus for i in equipped_items(character))


def effective_defense(character):
    return character.defense + sum(i.defense_bonus for i in equipped_items(character))


def gain_xp(character, amount, on_level_up=None):
    """Mutates `character` in place (caller commits -- or this commits
    itself via db.session, same as every other mutator in this
    module). `on_level_up(new_level)` is called once per level gained,
    for the caller to print a message -- this module has no session/
    write() access of its own, matching the existing convention that
    pure game-state helpers don't own I/O.

    XP curve ported verbatim from robot_rpg's own gain_xp(): start
    xp_next as whatever the character already has (20 by default, see
    MudCharacter's own column default), multiply by 1.5 per level,
    full heal (HP and mana) on level-up. The STAT gains themselves
    (hp_max/attack/defense/mana_max) are class-specific -- see
    classes.py's own level_up_stats() -- rather than one flat curve
    for every class."""
    from ...models import db
    character.xp += amount
    while character.xp >= character.xp_next:
        character.xp -= character.xp_next
        character.level += 1
        character.xp_next = int(character.xp_next * 1.5)
        hp_gain, attack_gain, defense_gain, mana_gain = _classes.level_up_stats(character)
        character.hp_max += hp_gain
        character.hp = character.hp_max
        character.attack += attack_gain
        character.defense += defense_gain
        character.mana_max += mana_gain
        character.mana = character.mana_max
        if on_level_up:
            on_level_up(character.level)
    db.session.commit()


# ---------------------------------------------------------------------------
# Quests -- quests_json is the same JSON-blob-per-character-progress
# pattern as inventory_json, referencing MudQuestTemplate.slug (the
# admin-editable catalog). See MudCharacter's own docstring for the
# exact per-entry shape.
# ---------------------------------------------------------------------------

def quests(character):
    try:
        data = json.loads(character.quests_json or '[]')
        if isinstance(data, list):
            return data
    except (ValueError, TypeError):
        pass
    return []


def _save_quests(character, qs):
    from ...models import db
    character.quests_json = json.dumps(qs)
    db.session.commit()


def find_quest_entry(character, quest_slug):
    for entry in quests(character):
        if entry.get('quest_slug') == quest_slug:
            return entry
    return None


def accept_quest(character, quest_slug):
    """Returns False if already accepted (or completed) -- True once
    newly added to the character's quest list."""
    if find_quest_entry(character, quest_slug) is not None:
        return False
    qs = quests(character)
    qs.append({'quest_slug': quest_slug, 'progress': 0,
              'completed': False, 'turned_in': False})
    _save_quests(character, qs)
    return True


def record_mob_kill(character, mob_slug):
    """Called after every combat win (combat.py) -- bumps progress on
    every ACTIVE, not-yet-completed quest whose target_mob_slug
    matches, across every quest the character has accepted (a mob kill
    counts toward every matching quest at once, not just one)."""
    from ...models import MudQuestTemplate
    qs = quests(character)
    changed = False
    for entry in qs:
        if entry.get('completed'):
            continue
        template = MudQuestTemplate.query.filter_by(
            slug=entry['quest_slug']).first()
        if template is None or template.target_mob_slug != mob_slug:
            continue
        entry['progress'] = entry.get('progress', 0) + 1
        if entry['progress'] >= template.target_count:
            entry['completed'] = True
        changed = True
    if changed:
        _save_quests(character, qs)


def turn_in_quest(character, quest_slug):
    """Grants the reward and marks turned_in -- returns the
    MudQuestTemplate row on success, None if the quest isn't accepted,
    isn't yet completed, or was already turned in."""
    from ...models import db, MudQuestTemplate
    entry = find_quest_entry(character, quest_slug)
    if entry is None or not entry.get('completed') or entry.get('turned_in'):
        return None
    template = MudQuestTemplate.query.filter_by(slug=quest_slug).first()
    if template is None:
        return None
    qs = quests(character)
    for e in qs:
        if e['quest_slug'] == quest_slug:
            e['turned_in'] = True
    _save_quests(character, qs)
    character.gold += template.reward_gold
    db.session.commit()
    gain_xp(character, template.reward_xp)
    if template.reward_item_slug:
        add_item(character, template.reward_item_slug)
    return template
