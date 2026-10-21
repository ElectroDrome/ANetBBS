# anetbbs/features/mud/seed.py
"""The Shattered Realm's starting world -- fully idempotent (per-slug
existence checks, same pattern every other self-healing seed block in
web_app.py already uses), safe to call on every boot.

Four zones: Village (safe, shop + healer), Forest (levels 1-5), Dungeon
(levels 5-12), and the Ruined Citadel (levels 12-20, reached down from
Dungeon's Sealed Vault). Exact room prose/stat numbers are content, not
architecture -- written directly here rather than pre-specified in the
design plan, same as every other door game's content in this codebase.
"""

MOBS = [
    # slug, name, level, hp, attack, defense, xp_reward, gold_reward
    ('giant-rat', 'Giant Rat', 1, 8, 2, 0, 8, 3,
     'A mangy rat the size of a house cat, teeth bared.'),
    ('wolf', 'Wolf', 3, 18, 5, 1, 18, 8,
     'A lean grey wolf, hackles raised, circling for an opening.'),
    ('bandit', 'Bandit', 5, 28, 7, 2, 30, 15,
     'A road bandit in patched leather, rusty blade in hand.'),
    ('skeleton', 'Skeleton', 6, 35, 8, 3, 40, 18,
     'Bones held together by old magic, blade raised in silence.'),
    ('goblin', 'Goblin', 8, 45, 10, 4, 55, 25,
     'A snarling goblin, crude spear gripped in both hands.'),
    ('ogre', 'Ogre', 12, 90, 16, 6, 150, 80,
     'A towering ogre, its club the size of a tree trunk.'),

    # Ruined Citadel -- levels 12-20.
    ('wraith', 'Wraith', 13, 100, 17, 6, 160, 70,
     'A translucent, shrieking spirit bound to this place.'),
    ('citadel-sentinel', 'Citadel Sentinel', 15, 130, 20, 8, 200, 90,
     'An animated suit of ancient plate armor, sword raised.'),
    ('dark-knight', 'Dark Knight', 17, 160, 24, 10, 260, 120,
     'A fallen knight clad in black steel, eyes burning cold.'),
    ('lich', 'The Lich King', 20, 220, 30, 12, 500, 300,
     'An undead sorcerer-king, crown still resting on a bare skull.'),
]

ITEMS = [
    # slug, name, item_type, attack_bonus, defense_bonus, heal_amount, value, is_shop_stock, description, required_class
    ('short-sword', 'Short Sword', 'weapon', 3, 0, 0, 20, True,
     'A well-balanced short sword, serviceable and sharp.', None),
    ('steel-sword', 'Steel Sword', 'weapon', 6, 0, 0, 60, True,
     'A heavier steel blade, favored by seasoned fighters.', None),
    ('leather-armor', 'Leather Armor', 'armor', 0, 2, 0, 18, True,
     'Boiled leather armor -- light and reasonably protective.', None),
    ('chainmail', 'Chainmail', 'armor', 0, 5, 0, 55, True,
     'A full suit of interlocking steel rings.', None),
    ('healing-potion', 'Healing Potion', 'consumable', 0, 0, 20, 10, True,
     'A small vial of shimmering red liquid.', None),
    ('greater-healing-potion', 'Greater Healing Potion', 'consumable', 0, 0, 50, 30, True,
     'A larger vial, glowing faintly -- a potent restorative.', None),

    # Ruined Citadel tier -- one signature weapon per class, plus
    # class-neutral top-end armor/consumable.
    ('warhammer-of-the-citadel', 'Warhammer of the Citadel', 'weapon', 12, 0, 0, 150, True,
     'A massive warhammer, etched with runes of old.', 'warrior'),
    ('archmage-staff', 'Archmage Staff', 'weapon', 10, 0, 0, 150, True,
     'A crystal-tipped staff humming with latent power.', 'mage'),
    ('shadow-daggers', 'Shadow Daggers', 'weapon', 11, 0, 0, 150, True,
     'Twin daggers that seem to drink the light around them.', 'rogue'),
    ('holy-mace', 'Holy Mace', 'weapon', 9, 0, 0, 150, True,
     'A blessed mace, warm to the touch.', 'cleric'),
    ('citadel-plate', 'Citadel Plate', 'armor', 0, 10, 0, 140, True,
     "Heavy plate armor recovered from the Citadel's fallen guardians.", None),
    ('elixir-of-vigor', 'Elixir of Vigor', 'consumable', 0, 0, 100, 60, True,
     'A potent elixir that mends even grievous wounds.', None),
]

# (slug, zone, name, description, mob_slug_or_None, feature_or_None)
ROOMS = [
    # Village -- safe zone.
    ('village-square', 'village', 'Village Square',
     'The heart of a small trading village. Cobblestones worn smooth '
     'by generations of foot traffic surround an old stone well at the '
     'center of everything.', None, None),
    ('village-north-street', 'village', 'North Street',
     'A quiet residential street lined with thatched-roof cottages.',
     None, None),
    ('village-south-street', 'village', 'South Street',
     'The street slopes gently downhill toward the village well.',
     None, None),
    ('village-east-street', 'village', 'East Street',
     "A bustling lane -- you can hear the shop's bell from here.",
     None, None),
    ('village-west-gate', 'village', 'West Gate',
     'A weathered wooden gate marks the edge of the village, beyond '
     'which lies the forest.', None, None),
    ('village-inn', 'village', 'The Weary Traveler Inn',
     'A warm, low-ceilinged inn, the smell of fresh bread in the air.',
     None, None),
    ('village-chapel', 'village', 'Village Chapel',
     'A small stone chapel, quiet and cool, candles flickering at the altar.',
     None, None),
    ('village-well', 'village', 'The Old Well',
     "An ancient well, said to be older than the village itself.",
     None, None),
    ('village-healer', 'village', "The Healer's Cottage",
     "A cozy cottage thick with the smell of herbs and poultices.",
     None, 'healer'),
    ('village-shop', 'village', 'General Store',
     'Shelves packed with weapons, armor, and traveling supplies.',
     None, 'shop'),

    # Forest -- levels 1-5.
    ('forest-entrance', 'forest', 'Forest Entrance',
     'The trees close in here, sunlight dappling through the canopy.',
     None, None),
    ('forest-path-1', 'forest', 'Winding Path',
     'A narrow path winds deeper into the woods.', 'giant-rat', None),
    ('forest-clearing', 'forest', 'Sunlit Clearing',
     'A small clearing where wildflowers grow in patches of sunlight.',
     'giant-rat', None),
    ('forest-thicket', 'forest', 'Dense Thicket',
     'Thorny undergrowth makes the going slow here.', 'wolf', None),
    ('forest-stream', 'forest', 'Babbling Stream',
     'A clear stream cuts through the forest floor.', 'wolf', None),
    ('forest-path-2', 'forest', 'Deeper Path',
     'The canopy grows thicker, and the path grows darker.', None, None),
    ('forest-hollow', 'forest', 'Shadowed Hollow',
     'A low hollow between two hills, strangely quiet.', 'wolf', None),
    ('forest-camp', 'forest', 'Abandoned Camp',
     'The remains of a campfire and a few scattered supplies.',
     'bandit', None),
    ('forest-path-3', 'forest', 'Far Path',
     'The trees here are old and gnarled, their roots breaking the trail.',
     'bandit', None),
    ('forest-edge', 'forest', 'Forest Edge',
     'The trees thin out ahead, giving way to broken, rocky ground.',
     None, None),

    # Dungeon -- levels 5-12.
    ('dungeon-entrance', 'dungeon', 'Dungeon Entrance',
     'A crumbling stone archway leads down into darkness.', None, None),
    ('dungeon-hall-1', 'dungeon', 'Entry Hall',
     'A long hall, torches guttering in ancient sconces.', None, None),
    ('dungeon-cell', 'dungeon', 'Old Prison Cell',
     'A rusted cell door hangs open on broken hinges.', 'skeleton', None),
    ('dungeon-hall-2', 'dungeon', 'Second Hall',
     'The hall widens here, old banners rotted to tatters on the walls.',
     None, None),
    ('dungeon-armory', 'dungeon', 'Ruined Armory',
     'Broken weapon racks line the walls, mostly picked clean.',
     'goblin', None),
    ('dungeon-crossing', 'dungeon', 'Four-Way Crossing',
     'Passages lead off in every direction from this junction.',
     None, None),
    ('dungeon-shrine', 'dungeon', 'Forgotten Shrine',
     'A small shrine to a long-forgotten god, cracked and cold.',
     'goblin', None),
    ('dungeon-pit', 'dungeon', 'Sunken Pit',
     'The floor drops away into a shadowed pit.', 'skeleton', None),
    ('dungeon-throne', 'dungeon', 'Throne Room',
     'A once-grand throne room, now home to something far less regal.',
     'ogre', None),
    ('dungeon-vault', 'dungeon', 'Sealed Vault',
     'A heavy vault door stands ajar, long since forced open by something strong.',
     'ogre', None),

    # Ruined Citadel -- levels 12-20, reached down from the Sealed Vault.
    ('citadel-gate', 'citadel', 'Citadel Gate',
     'Massive iron gates, rusted open, guard the entrance to the Ruined Citadel.',
     None, None),
    ('citadel-courtyard', 'citadel', 'Shattered Courtyard',
     'Broken statues litter a courtyard choked with weeds.', 'wraith', None),
    ('citadel-barracks', 'citadel', 'Abandoned Barracks',
     'Rows of rotted bunks, long since emptied.', 'wraith', None),
    ('citadel-hall', 'citadel', 'Grand Hall',
     'A vast hall, its vaulted ceiling lost in shadow.',
     'citadel-sentinel', None),
    ('citadel-armory', 'citadel', 'Citadel Armory',
     'Racks of ancient weapons, most rusted beyond use.',
     'citadel-sentinel', None),
    ('citadel-crypt', 'citadel', 'Sunken Crypt',
     'Tombs lie cracked open, their occupants long gone.', 'dark-knight', None),
    ('citadel-vault-inner', 'citadel', 'Inner Vault',
     'A heavily warded vault, its seals broken by something immense.',
     'dark-knight', None),
    ('citadel-throne-ruins', 'citadel', 'Ruined Throne',
     'The remains of a once-mighty throne, shattered by time.', None, None),
    ('citadel-sanctum', 'citadel', "The Lich's Sanctum",
     'A cold, still chamber where dark power still lingers.', 'lich', None),
]

# (slug, name, giver_room_slug, target_mob_slug, target_count, reward_xp,
# reward_gold, reward_item_slug_or_None)
QUESTS = [
    ('cleanse-the-citadel', 'Cleanse the Citadel', 'village-chapel',
     'wraith', 3, 150, 75, 'citadel-plate'),
]

# (room_slug, direction, target_room_slug) -- each direction listed
# explicitly in both directions where the connection is two-way (the
# normal case); a few are deliberately one-way (see MudExit's own
# docstring for why that's a valid, supported shape, not an oversight).
EXITS = [
    ('village-square', 'north', 'village-north-street'),
    ('village-north-street', 'south', 'village-square'),
    ('village-square', 'south', 'village-south-street'),
    ('village-south-street', 'north', 'village-square'),
    ('village-square', 'east', 'village-east-street'),
    ('village-east-street', 'west', 'village-square'),
    ('village-square', 'west', 'village-west-gate'),
    ('village-west-gate', 'east', 'village-square'),

    ('village-north-street', 'north', 'village-inn'),
    ('village-inn', 'south', 'village-north-street'),
    ('village-north-street', 'east', 'village-chapel'),
    ('village-chapel', 'west', 'village-north-street'),

    ('village-south-street', 'south', 'village-well'),
    ('village-well', 'north', 'village-south-street'),
    ('village-south-street', 'west', 'village-healer'),
    ('village-healer', 'east', 'village-south-street'),

    ('village-east-street', 'east', 'village-shop'),
    ('village-shop', 'west', 'village-east-street'),

    ('village-west-gate', 'west', 'forest-entrance'),
    ('forest-entrance', 'east', 'village-west-gate'),

    ('forest-entrance', 'west', 'forest-path-1'),
    ('forest-path-1', 'east', 'forest-entrance'),
    ('forest-path-1', 'north', 'forest-clearing'),
    ('forest-clearing', 'south', 'forest-path-1'),
    ('forest-clearing', 'east', 'forest-thicket'),
    ('forest-thicket', 'west', 'forest-clearing'),
    ('forest-thicket', 'north', 'forest-stream'),
    ('forest-stream', 'south', 'forest-thicket'),
    ('forest-stream', 'west', 'forest-camp'),
    ('forest-camp', 'east', 'forest-stream'),

    ('forest-path-1', 'west', 'forest-path-2'),
    ('forest-path-2', 'east', 'forest-path-1'),
    ('forest-path-2', 'south', 'forest-hollow'),
    ('forest-hollow', 'north', 'forest-path-2'),
    ('forest-path-2', 'west', 'forest-path-3'),
    ('forest-path-3', 'east', 'forest-path-2'),
    ('forest-path-3', 'west', 'forest-edge'),
    ('forest-edge', 'east', 'forest-path-3'),

    ('forest-edge', 'west', 'dungeon-entrance'),
    ('dungeon-entrance', 'east', 'forest-edge'),

    ('dungeon-entrance', 'west', 'dungeon-hall-1'),
    ('dungeon-hall-1', 'east', 'dungeon-entrance'),
    ('dungeon-hall-1', 'north', 'dungeon-cell'),
    ('dungeon-cell', 'south', 'dungeon-hall-1'),
    ('dungeon-hall-1', 'west', 'dungeon-hall-2'),
    ('dungeon-hall-2', 'east', 'dungeon-hall-1'),
    ('dungeon-hall-2', 'south', 'dungeon-armory'),
    ('dungeon-armory', 'north', 'dungeon-hall-2'),
    ('dungeon-hall-2', 'west', 'dungeon-crossing'),
    ('dungeon-crossing', 'east', 'dungeon-hall-2'),

    ('dungeon-crossing', 'north', 'dungeon-shrine'),
    ('dungeon-shrine', 'south', 'dungeon-crossing'),
    ('dungeon-crossing', 'south', 'dungeon-pit'),
    ('dungeon-pit', 'north', 'dungeon-crossing'),
    ('dungeon-crossing', 'west', 'dungeon-throne'),
    ('dungeon-throne', 'east', 'dungeon-crossing'),
    ('dungeon-throne', 'west', 'dungeon-vault'),
    ('dungeon-vault', 'east', 'dungeon-throne'),

    ('dungeon-vault', 'down', 'citadel-gate'),
    ('citadel-gate', 'up', 'dungeon-vault'),

    ('citadel-gate', 'north', 'citadel-courtyard'),
    ('citadel-courtyard', 'south', 'citadel-gate'),
    ('citadel-courtyard', 'east', 'citadel-barracks'),
    ('citadel-barracks', 'west', 'citadel-courtyard'),
    ('citadel-courtyard', 'north', 'citadel-hall'),
    ('citadel-hall', 'south', 'citadel-courtyard'),
    ('citadel-hall', 'east', 'citadel-armory'),
    ('citadel-armory', 'west', 'citadel-hall'),
    ('citadel-hall', 'west', 'citadel-crypt'),
    ('citadel-crypt', 'east', 'citadel-hall'),
    ('citadel-hall', 'north', 'citadel-throne-ruins'),
    ('citadel-throne-ruins', 'south', 'citadel-hall'),
    ('citadel-throne-ruins', 'west', 'citadel-vault-inner'),
    ('citadel-vault-inner', 'east', 'citadel-throne-ruins'),
    ('citadel-throne-ruins', 'north', 'citadel-sanctum'),
    ('citadel-sanctum', 'south', 'citadel-throne-ruins'),
]


def seed_world():
    from ...models import (db, MudMobTemplate, MudItemTemplate, MudRoom,
                           MudExit, MudQuestTemplate)

    mob_by_slug = {}
    for slug, name, level, hp, attack, defense, xp, gold, desc in MOBS:
        mob = MudMobTemplate.query.filter_by(slug=slug).first()
        if mob is None:
            mob = MudMobTemplate(slug=slug, name=name, level=level, hp=hp,
                                 attack=attack, defense=defense,
                                 xp_reward=xp, gold_reward=gold,
                                 description=desc)
            db.session.add(mob)
        mob_by_slug[slug] = mob
    db.session.commit()

    for (slug, name, item_type, atk, dfn, heal, value, stock, desc,
        required_class) in ITEMS:
        if MudItemTemplate.query.filter_by(slug=slug).first() is None:
            db.session.add(MudItemTemplate(
                slug=slug, name=name, item_type=item_type,
                attack_bonus=atk, defense_bonus=dfn, heal_amount=heal,
                value=value, is_shop_stock=stock, description=desc,
                required_class=required_class))
    db.session.commit()

    room_by_slug = {}
    for slug, zone, name, desc, mob_slug, feature in ROOMS:
        room = MudRoom.query.filter_by(slug=slug).first()
        if room is None:
            room = MudRoom(
                slug=slug, zone=zone, name=name, description=desc,
                mob_template_id=mob_by_slug[mob_slug].id if mob_slug else None,
                feature=feature)
            db.session.add(room)
        room_by_slug[slug] = room
    db.session.commit()

    for room_slug, direction, target_slug in EXITS:
        room = room_by_slug[room_slug]
        existing = MudExit.query.filter_by(
            room_id=room.id, direction=direction).first()
        if existing is None:
            db.session.add(MudExit(
                room_id=room.id, direction=direction,
                target_room_id=room_by_slug[target_slug].id))
    db.session.commit()

    for (slug, name, giver_room_slug, target_mob_slug, target_count,
        reward_xp, reward_gold, reward_item_slug) in QUESTS:
        if MudQuestTemplate.query.filter_by(slug=slug).first() is None:
            db.session.add(MudQuestTemplate(
                slug=slug, name=name,
                giver_room_id=room_by_slug[giver_room_slug].id,
                target_mob_slug=target_mob_slug, target_count=target_count,
                reward_xp=reward_xp, reward_gold=reward_gold,
                reward_item_slug=reward_item_slug))
    db.session.commit()
