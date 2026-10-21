# anetbbs/features/mud/combat.py
"""Turn-based combat -- damage resolution ported verbatim from
data/mods/doors/robot_rpg/combat.py's own proven _resolve_attack(),
turn loop adapted from its run_battle() (player, then mob, repeat),
extended with class-specific abilities (classes.py): Warrior's Power
Attack (bonus damage, cooldown-gated), Mage's Fireball (mana-gated
magic damage that ignores physical defense), Rogue's Backstab (bonus
damage, opening-move only), Cleric's Heal (mana-gated, restores HP
instead of attacking).

Mobs are INSTANCED per player (a MobInstance is plain in-memory state,
built fresh from a MudMobTemplate each encounter, never persisted or
synced across processes) -- a deliberate scope choice, not a missing
feature: see docs/40-mud.md's "Multiplayer" section. This also means
combat here needs no authority-election/shared-HP-sync machinery at
all, unlike anetcraft.py's own mob combat.
"""
import random

from . import character as _char
from . import classes as _classes


def resolve_attack(attack, defense, defending_bonus=0):
    """Verbatim port of robot_rpg/combat.py's _resolve_attack()."""
    base = max(1, attack - defense - defending_bonus)
    variance = random.randint(-1, 2)
    return max(1, base + variance)


class MobInstance:
    """One player's own private copy of a room's wandering mob for a
    single encounter -- built fresh from a MudMobTemplate, discarded
    when the fight ends (won, fled, or the player died). Never shared
    with or visible to any other player/process."""

    def __init__(self, template):
        self.template = template
        self.name = template.name
        self.hp = template.hp
        self.hp_max = template.hp
        self.attack = template.attack
        self.defense = template.defense
        self.xp_reward = template.xp_reward
        self.gold_reward = template.gold_reward


async def _try_ability(session, character, mob, class_info, turn):
    """Attempts the character's class ability. Returns True if it
    actually resolved (consuming the turn), False if it was
    unavailable right now (wrong cooldown window, not enough mana, or
    backstab past the opening move) -- a False result means the caller
    should re-prompt without advancing the turn counter or letting the
    mob act, same as any other invalid input."""
    cls = character.char_class
    if cls == 'warrior':
        if turn % _classes.POWER_ATTACK_COOLDOWN == 0:
            dmg = int(resolve_attack(_char.effective_attack(character), mob.defense)
                      * _classes.POWER_ATTACK_MULTIPLIER)
            mob.hp -= dmg
            await session.write(f"\r\nPower Attack! You hit the {mob.name} for {dmg} damage.\r\n")
            return True
        turns_left = _classes.POWER_ATTACK_COOLDOWN - (turn % _classes.POWER_ATTACK_COOLDOWN)
        await session.write(
            f"\r\nPower Attack isn't ready yet ({turns_left} more turn(s)).\r\n")
        return False

    if cls == 'mage':
        if character.mana >= _classes.FIREBALL_MANA_COST:
            character.mana -= _classes.FIREBALL_MANA_COST
            dmg = max(1, int(_char.effective_attack(character)
                            * _classes.FIREBALL_MULTIPLIER) + random.randint(-1, 2))
            mob.hp -= dmg
            await session.write(f"\r\nFireball! You hit the {mob.name} for {dmg} damage.\r\n")
            return True
        await session.write("\r\nNot enough mana to cast Fireball.\r\n")
        return False

    if cls == 'rogue':
        if turn == 0:
            dmg = int(resolve_attack(_char.effective_attack(character), mob.defense)
                      * _classes.BACKSTAB_MULTIPLIER)
            mob.hp -= dmg
            await session.write(f"\r\nBackstab! You hit the {mob.name} for {dmg} damage.\r\n")
            return True
        await session.write("\r\nYou can only Backstab as your opening strike.\r\n")
        return False

    if cls == 'cleric':
        if character.mana >= _classes.HEAL_MANA_COST:
            character.mana -= _classes.HEAL_MANA_COST
            heal_amt = _classes.HEAL_AMOUNT_BASE + _classes.HEAL_AMOUNT_PER_LEVEL * character.level
            character.hp = min(character.hp_max, character.hp + heal_amt)
            await session.write(f"\r\nYou heal for {heal_amt} HP.\r\n")
            return True
        await session.write("\r\nNot enough mana to Heal.\r\n")
        return False

    return False


async def run_combat(session, character, mob):
    """Interactive turn-based fight. Returns one of 'won', 'fled',
    'died'. On 'won', XP/gold are already granted, quest progress is
    recorded, and `character` is already committed; on 'died', the
    character's hp is left at a small positive value and they're
    returned to the starting room by the caller (MUD death has no
    permadeath/item-loss penalty in this build -- a deliberate,
    forgiving choice for a BBS door game, not a half-built death-
    penalty system)."""
    from ...models import db

    class_info = _classes.class_info(character.char_class)
    ability_verb = class_info['ability_verb']
    ability_key = ability_verb[0].upper()
    turn = 0

    await session.write(f"\r\n\x1b[1;31mA {mob.name} attacks!\x1b[0m\r\n")
    while True:
        mana_line = (f"   Mana: {character.mana}/{character.mana_max}"
                    if character.mana_max else '')
        await session.write(
            f"\r\n{mob.name}: {mob.hp}/{mob.hp_max} HP   "
            f"You: {character.hp}/{character.hp_max} HP{mana_line}\r\n"
            f"[A]ttack  [{ability_key}]{ability_verb[1:]} "
            f"({class_info['ability_name']})  [F]lee: ")
        choice = (await session.read_line('') or '').strip().lower()

        acted = False
        if choice in ('f', 'flee'):
            if random.random() < _classes.flee_chance(character):
                await session.write("\r\nYou flee from the fight!\r\n")
                return 'fled'
            await session.write("\r\nYou couldn't get away!\r\n")
            acted = True
        elif choice in ('a', 'attack', ''):
            dmg = resolve_attack(_char.effective_attack(character), mob.defense)
            mob.hp -= dmg
            await session.write(f"\r\nYou hit the {mob.name} for {dmg} damage.\r\n")
            acted = True
        elif choice in (ability_verb, ability_verb[0]):
            acted = await _try_ability(session, character, mob, class_info, turn)
        else:
            await session.write(
                f"\r\nAttack, flee, or {ability_verb} ({class_info['ability_name']}) "
                f"-- pick one.\r\n")

        if not acted:
            continue  # ability unavailable or bad input -- re-prompt, no turn spent

        turn += 1
        db.session.commit()

        if mob.hp <= 0:
            await session.write(f"\r\n\x1b[1;32mYou defeated the {mob.name}!\x1b[0m\r\n")
            character.gold += mob.gold_reward
            await session.write(
                f"You gain {mob.xp_reward} XP and {mob.gold_reward} gold.\r\n")
            db.session.commit()
            _char.gain_xp(character, mob.xp_reward, on_level_up=lambda lvl: None)
            _char.record_mob_kill(character, mob.template.slug)
            return 'won'

        # Mob's turn (even after a successful Heal, the mob still acts).
        dmg = resolve_attack(mob.attack, _char.effective_defense(character))
        character.hp -= dmg
        db.session.commit()
        await session.write(f"The {mob.name} hits you for {dmg} damage.\r\n")
        if character.hp <= 0:
            await session.write(
                "\r\n\x1b[1;31mYou have been defeated...\x1b[0m\r\n"
                "Everything goes dark.\r\n")
            character.hp = 1
            db.session.commit()
            return 'died'
