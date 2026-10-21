# anetbbs/features/doorparty_import.py
"""
DoorParty (dp.throwbackbbs.com) Game-row builder -- Phase 4 of the
door-games roadmap (2026-10-20).

Real setup confirmed directly from DoorParty's own Mystic/Linux setup
HOWTO (wiki.throwbackbbs.com, pasted in full by Jerry): a sysop runs a
separate local daemon, DoorParty Connector
(github.com/echicken/doorparty-connector), configured with the
sysop's real DoorParty SSH credentials and a local listen port. That
daemon holds the real DoorParty credentials and does the actual
upstream SSH/RLogin connection to dp.throwbackbbs.com -- ANetBBS
itself never needs to know them. ANetBBS's own job is an ORDINARY
LOCAL rlogin connection (the existing, unmodified door_rlogin Game-row
machinery already does this) to 127.0.0.1:<local port>.

The real documented Mystic menu example:
    /ADDR=127.0.0.1 /PORT=9999 /USER=[NEW]@USER@ /PASS=RANDOM_CHARS
where [NEW] is the sysop's own assigned DoorParty BBS tag (a literal
PREFIX with NO separator before Mystic's own @USER@ substitution
token) and RANDOM_CHARS is a local shared secret the sysop makes up
themselves (verified only between ANetBBS and the local connector,
never sent to DoorParty itself).

This is DELIBERATELY NOT the same shape as ANetBBS's own existing
`Game.rlogin_bbs_tag` field (anetbbs/games/rlogin_bridge.py's
build_client_user(), which joins a hyphen SUFFIX for a different
target -- Jerry's own A-Net Game Server / a generic Synchronet rlogin
game-server). DoorParty's connector specifically expects the
prefix-no-separator shape its own setup docs show, so the tag is baked
directly into `command_line_args` via the existing @USER@ template
token instead, and `rlogin_bbs_tag` is left unused (empty) for a
DoorParty Game row.

No bulk door-code catalog import here (unlike
anet_game_import.py) -- DoorParty's full door catalog is reached
through ONE connection point (matching ENiGMA1/2's own `door_party`
module needing just one set of credentials, not one per door, and the
Mystic example itself being a single generic menu entry, not a
per-door one), with door selection happening on DoorParty's own remote
menu once connected. A sysop who knows a specific DoorParty door code
supports Synchronet-style xtrn=<code> direct launch can optionally
supply one when adding a game -- flagged as unconfirmed for DoorParty
specifically (only confirmed for a Synchronet-style hub, e.g. Jerry's
own A-Net Game Server) rather than asserted as fact.
"""
import re


def slug_for_name(name, door_code=''):
    """A display name (+ optional door code) -> a unique Game.slug.
    The 'doorparty-' prefix avoids colliding with a sysop's own
    locally-added door sharing a similar short name."""
    basis = door_code or name
    cleaned = re.sub(r'[^a-z0-9]+', '-', basis.lower()).strip('-')
    return f'doorparty-{cleaned}' if cleaned else None


def build_doorparty_game_kwargs(name, door_code='', local_port='',
                                bbs_tag='', shared_secret='',
                                category='other', max_nodes=4,
                                sort_order=0):
    """The exact Game(**kwargs) a DoorParty entry becomes -- a
    door_rlogin pointed at the sysop's own local DoorParty Connector,
    using the real "<TAG>@USER@ <secret>" wire shape documented above.

    `door_code`, if given, appends " xtrn=<code>" the same way
    anet_game_import.py's own build_game_kwargs() does for a
    Synchronet-style hub -- UNCONFIRMED whether DoorParty's own Mystic
    backend honors this the same way; leave door_code blank for the
    safe, confirmed default (DoorParty's own in-menu door picker).
    """
    client_user_template = f'{bbs_tag}@USER@'
    args = f'{client_user_template} {shared_secret}'
    if door_code:
        args += f' xtrn={door_code}'
    return {
        'name': name,
        'slug': slug_for_name(name, door_code),
        'description': 'DoorParty (dp.throwbackbbs.com), via local DoorParty Connector',
        'category': category,
        'game_type': 'door_rlogin',
        'executable_path': f'127.0.0.1:{local_port}',
        'command_line_args': args,
        'rlogin_bbs_tag': '',
        'max_nodes': max_nodes,
        'sort_order': sort_order,
        'is_active': True,
    }
