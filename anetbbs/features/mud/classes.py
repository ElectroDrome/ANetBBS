# anetbbs/features/mud/classes.py
"""Character classes -- chosen once at character creation, permanent.

Classes are code-defined here, NOT an admin-editable database table
(unlike rooms/items/mobs/quests): a class's actual BEHAVIOR (what its
ability does in combat, its resource type) is logic, not a flat stat
block a sysop could meaningfully reconfigure through a form. This
matches the same reasoning that kept the command parser (parser.py)
out of the database too.

Four classic, instantly-recognizable MUD/RPG archetypes -- the
"melee/caster/skirmisher/support" spread almost every RPG settles on,
chosen for legibility over novelty:

  - Warrior: tank/melee. Highest HP growth, no mana. Ability:
    Power Attack (bonus damage, usable every 3rd turn).
  - Mage: caster. Lowest HP growth, a real mana pool. Ability:
    Fireball (strong magic damage, ignores physical defense, costs
    mana).
  - Rogue: skirmisher. Best flee chance, no mana. Ability: Backstab
    (bonus damage, but only as the OPENING move of a fight).
  - Cleric: support/caster. Moderate HP, a mana pool. Ability: Heal
    (restores HP mid-fight instead of attacking that turn, costs
    mana) -- the one class that can out-sustain a long fight without
    retreating to the healer.
"""
CLASS_SLUGS = ('warrior', 'mage', 'rogue', 'cleric')

# Base stats at level 1 and per-level growth -- MudCharacter's own
# column defaults (attack=4, defense=1, hp=20/hp_max=20, mana=0/
# mana_max=0) are the Warrior's base; other classes override at
# creation time (see character.apply_class_base_stats()).
CLASSES = {
    'warrior': {
        'label': 'Warrior',
        'description': 'A battle-hardened fighter -- high HP, strong '
                       'in a straight fight, no spells.',
        'base_hp': 24, 'base_attack': 5, 'base_defense': 2,
        'base_mana': 0,
        'hp_per_level': 7, 'attack_per_level': 2, 'defense_per_level': 1,
        'mana_per_level': 0,
        'flee_chance': 0.6,
        'ability_name': 'Power Attack',
        'ability_verb': 'power',
        'ability_help': 'power -- a heavier blow, usable every 3rd turn',
    },
    'mage': {
        'label': 'Mage',
        'description': 'A fragile spellcaster -- low HP, but Fireball '
                       'hits far harder than any blade.',
        'base_hp': 14, 'base_attack': 2, 'base_defense': 0,
        'base_mana': 20,
        'hp_per_level': 3, 'attack_per_level': 1, 'defense_per_level': 0,
        'mana_per_level': 5,
        'flee_chance': 0.6,
        'ability_name': 'Fireball',
        'ability_verb': 'cast',
        'ability_help': 'cast -- Fireball: strong magic damage, costs 8 mana',
    },
    'rogue': {
        'label': 'Rogue',
        'description': 'Quick and elusive -- the best odds of escaping '
                       'a losing fight, and a nasty opening strike.',
        'base_hp': 18, 'base_attack': 4, 'base_defense': 1,
        'base_mana': 0,
        'hp_per_level': 5, 'attack_per_level': 2, 'defense_per_level': 1,
        'mana_per_level': 0,
        'flee_chance': 0.8,
        'ability_name': 'Backstab',
        'ability_verb': 'backstab',
        'ability_help': 'backstab -- double damage, but only as your opening move',
    },
    'cleric': {
        'label': 'Cleric',
        'description': 'A devoted healer -- can mend wounds mid-fight '
                       'instead of running back to town.',
        'base_hp': 18, 'base_attack': 3, 'base_defense': 1,
        'base_mana': 16,
        'hp_per_level': 5, 'attack_per_level': 1, 'defense_per_level': 1,
        'mana_per_level': 4,
        'flee_chance': 0.6,
        'ability_name': 'Heal',
        'ability_verb': 'heal',
        'ability_help': 'heal -- restore HP instead of attacking, costs 6 mana',
    },
}

FIREBALL_MANA_COST = 8
FIREBALL_MULTIPLIER = 1.6  # applied to effective_attack, ignores mob defense
HEAL_MANA_COST = 6
HEAL_AMOUNT_BASE = 16
HEAL_AMOUNT_PER_LEVEL = 2
POWER_ATTACK_COOLDOWN = 3
POWER_ATTACK_MULTIPLIER = 1.8
BACKSTAB_MULTIPLIER = 2.0


def class_info(char_class):
    return CLASSES.get(char_class, CLASSES['warrior'])


def apply_class_base_stats(character, char_class):
    """Called once, at character creation, to set starting stats for
    the chosen class (overriding MudCharacter's own column defaults,
    which are the Warrior's own base numbers)."""
    info = class_info(char_class)
    character.char_class = char_class
    character.hp = info['base_hp']
    character.hp_max = info['base_hp']
    character.attack = info['base_attack']
    character.defense = info['base_defense']
    character.mana = info['base_mana']
    character.mana_max = info['base_mana']


def level_up_stats(character):
    """Returns (hp_gain, attack_gain, defense_gain, mana_gain) for one
    level-up, per this character's own class growth table."""
    info = class_info(character.char_class)
    return (info['hp_per_level'], info['attack_per_level'],
           info['defense_per_level'], info['mana_per_level'])


def flee_chance(character):
    return class_info(character.char_class)['flee_chance']


def regen_mana(character, amount=2):
    """Called on movement between rooms (parser.py's _move()) -- slow
    passive mana regen outside combat, on top of a full restore at the
    healer (shop.py's run_healer())."""
    if character.mana_max <= 0:
        return
    character.mana = min(character.mana_max, character.mana + amount)
