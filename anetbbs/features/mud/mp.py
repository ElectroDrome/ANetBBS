# anetbbs/features/mud/mp.py
"""Cross-process presence + chat for The Shattered Realm.

Every door session -- builtin_python games included -- is spawned as
its own separate OS subprocess by door_runner.py/builtin_runner.py for
native SSH/telnet AND the web terminal alike (confirmed real,
previously-fixed bug: see anetbbs/features/anetcraft.py's own
multiplayer module docstring). A plain module-level dict would mean
"multiplayer" silently means "solo, with extra steps" -- two players
would each get their own private, empty in-memory roster.

Fixed the same proven way ANetCRAFT's own multiplayer is: shared state
lives on disk under a data/ directory, polled/appended each loop
iteration, scoped down to just presence + chat (no delta log, no mob/
snapshot sync) since mob encounters here are INSTANCED per player, not
a shared-HP mob synced across every process -- see docs/40-mud.md's
"Multiplayer" section for why that's a deliberate scope choice, not a
missing feature.
"""
import json
import time
from pathlib import Path
from urllib.parse import quote as _urlquote

SAVE_DIR = Path(__file__).parent.parent.parent.parent / 'data' / 'doors' / 'mud'
MP_STALE_SECONDS = 10.0


def _mp_root() -> Path:
    d = SAVE_DIR / 'mp'
    (d / 'players').mkdir(parents=True, exist_ok=True)
    return d


def _chat_path() -> Path:
    return _mp_root() / 'chat.jsonl'


def safe_username(username: str) -> str:
    """Percent-encode (not strip) a username for use as a filename
    component -- same injective-encoding fix anetcraft.py's own
    _safe_username() already established (stripping instead of
    encoding let distinct usernames collide onto the same save file;
    this codebase's registration form allows characters, like space
    and '.', that a naive strip would silently collapse)."""
    return _urlquote(username, safe='') or 'player'


def _player_path(username: str) -> Path:
    return _mp_root() / 'players' / f'{safe_username(username)}.json'


def publish_presence(username: str, room_slug: str, joined_at: float):
    """Write/refresh this player's own presence file. Call once on
    entry and again whenever the player's room changes (or at least
    every loop iteration -- cheap, matches anetcraft.py's own
    every-tick-republish convention)."""
    data = {
        'username': username,
        'room_slug': room_slug,
        'joined_at': joined_at,
        'ts': time.time(),
    }
    try:
        _player_path(username).write_text(json.dumps(data))
    except OSError:
        pass


def remove_presence(username: str):
    """Call on exit (try/finally) so a disconnected player doesn't
    linger as "online" for MP_STALE_SECONDS after they've actually
    left."""
    try:
        _player_path(username).unlink(missing_ok=True)
    except OSError:
        pass


def list_players() -> dict:
    """Every currently-fresh connected player's last-published
    presence, keyed by their real (unsanitized) username -- {} if none
    are online. Includes the caller's own entry if they've already
    published one; filter it out by username if "others only" is
    wanted (matches anetcraft.py's own _mp_list_players() semantics)."""
    out = {}
    now = time.time()
    try:
        entries = list((_mp_root() / 'players').iterdir())
    except FileNotFoundError:
        return out
    for f in entries:
        if f.suffix != '.json':
            continue
        try:
            pdata = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if now - pdata.get('ts', 0) > MP_STALE_SECONDS:
            continue
        uname = pdata.get('username')
        if uname:
            out[uname] = pdata
    return out


def players_in_room(room_slug: str, exclude_username: str = '') -> list:
    """Usernames of everyone else currently in `room_slug`."""
    return sorted(
        uname for uname, pdata in list_players().items()
        if pdata.get('room_slug') == room_slug and uname != exclude_username)


def live_player_count() -> int:
    return len(list_players())


class ChatTail:
    """Tails chat.jsonl from wherever this instance last left off --
    one instance per player session, same incremental-read pattern as
    anetcraft.py's own _mp_tail_chat()/_mp_send_chat()."""

    def __init__(self):
        self._pos = 0

    def send(self, username: str, kind: str, text: str, target: str = ''):
        """kind: 'say' (room-scoped by the reader, not here -- this
        log has no room concept, callers filter by checking the
        sender's room themselves), 'yell' (whole-MUD), 'tell'
        (private, `target` is the recipient), 'emote'."""
        try:
            with open(_chat_path(), 'a') as f:
                f.write(json.dumps([username, kind, text, target]) + '\n')
        except OSError:
            pass

    def poll(self):
        """Returns new (username, kind, text, target) tuples published
        since the last poll() call on this instance."""
        try:
            raw = _chat_path().read_bytes()
        except FileNotFoundError:
            return []
        if self._pos > len(raw):
            self._pos = 0  # log was rotated/truncated
        chunk = raw[self._pos:]
        self._pos = len(raw)
        out = []
        for line in chunk.split(b'\n'):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                username, kind, text, target = parsed
            except (ValueError, TypeError):
                continue
            out.append((username, kind, text, target))
        return out
