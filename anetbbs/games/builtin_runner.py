"""
Subprocess wrapper for builtin_python BBS games.

Spawned by door_runner.py as a PTY child process.  Creates a thin session
shim over stdin/stdout so game coroutines work unchanged in the web sandbox.

Usage: builtin_runner.py <module:function>
  e.g.  anetbbs.features.anetcraft:launch_anetcraft
  BBS_USERNAME env var supplies the player's username.

Key design decisions
--------------------
* stdin-relay thread + internal pipe:
    asyncio.loop.connect_read_pipe() sets O_NONBLOCK on whatever fd it
    receives.  fd 0 (PTY slave stdin) and fd 1 (PTY slave stdout) share
    the SAME open-file-description (created by os.dup2 in door_runner.py),
    so setting O_NONBLOCK on fd 0 would make fd 1 non-blocking too.
    Non-blocking writes to a PTY silently truncate when the PTY buffer is
    full, producing a black screen on the first (large) frame.
    Solution: a blocking thread reads from fd 0 and forwards into an
    ordinary pipe (pipe_r/pipe_w).  asyncio connects to pipe_r, so
    O_NONBLOCK is set on the pipe — not on the PTY fd.

* chunked async stdout writes:
    os.write(1, large_frame) can block the asyncio event loop thread.
    Writing in 4096-byte slices with run_in_executor() keeps the loop
    responsive and prevents time-outs from firing mid-write.
"""
import asyncio
import os
import sys
import termios
import threading
import tty


class _StdioSession:
    """Minimal BBS session shim backed by stdin (via relay pipe) / stdout."""

    def __init__(self, reader: asyncio.StreamReader):
        self._reader = reader
        _username = os.environ.get('BBS_USERNAME', 'player')
        try:
            _user_id = int(os.environ.get('BBS_USER_ID', '0') or 0) or None
        except ValueError:
            _user_id = None
        self.user = {
            'id':           _user_id,
            'username':     _username,
            'display_name': os.environ.get('BBS_REAL_NAME', _username),
            'access_level': int(os.environ.get('BBS_SECURITY', '50')),
        }

    async def write(self, text: str):
        data = text.encode('cp437', errors='replace')
        loop = asyncio.get_running_loop()
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 4096]
            n = await loop.run_in_executor(None, os.write, 1, chunk)
            offset += max(n, 1)

    async def read_raw(self, n: int = 64) -> bytes:
        return await self._reader.read(n)

    async def read_line(self, prompt: str = '') -> str:
        """Real bug found live: this never echoed typed input at all --
        door_runner.py puts the PTY slave in raw mode (tty.setraw(0)) so
        there's no terminal-level local echo to fall back on either, the
        same reasoning real BBSSession.read_line() documents for why IT
        echoes explicitly (anetbbs/core/session.py). A player typing
        their character name or class choice saw nothing appear at all.
        Mirrors that method's own echo convention exactly: echo each
        printable character as it's read, echo '\\r\\n' on Enter, and the
        same backspace erase-in-place sequence this already had."""
        if prompt:
            await self.write(prompt)
        line = b''
        while True:
            ch = await self._reader.read(1)
            if not ch or ch in (b'\r', b'\n'):
                await self.write('\r\n')
                break
            if ch in (b'\x08', b'\x7f'):
                if line:
                    line = line[:-1]
                    await self.write('\x08 \x08')
            else:
                line += ch
                await self.write(ch.decode('cp437', errors='replace'))
        return line.decode('cp437', errors='replace')


async def _run(fn, username: str, app):
    """Runs `fn` for the whole interactive game session inside a pushed
    Flask app context -- this subprocess has no ambient Flask app of its
    own (unlike the in-process telnet/SSH/rlogin Game Center path, which
    runs inside anetbbs.service's own long-lived app context), so any
    builtin_python game that touches the database (MudCharacter.query,
    etc. -- the first one that actually needed this) would otherwise hit
    "Working outside of application context" the instant it queried.
    Confirmed real: this is exactly what broke The Shattered Realm when
    launched from the web UI even after the earlier BBS_USER_ID fix.
    """
    from anetbbs.features.db_scope import transient_app_context
    with transient_app_context(app):
        await _play(fn, username)


async def _play(fn, username: str):
    loop = asyncio.get_running_loop()

    # Pipe-based stdin relay: a background thread blocks on os.read(0)
    # and forwards bytes into pipe_w.  asyncio reads from pipe_r.
    # This keeps O_NONBLOCK off the PTY file description (which is shared
    # by both fd 0 and fd 1).
    pipe_r, pipe_w = os.pipe()

    def _stdin_relay():
        try:
            while True:
                chunk = os.read(0, 256)
                if not chunk:
                    break
                os.write(pipe_w, chunk)
        except OSError:
            pass
        finally:
            try:
                os.close(pipe_w)
            except OSError:
                pass

    threading.Thread(target=_stdin_relay, daemon=True,
                     name='stdin-relay').start()

    reader = asyncio.StreamReader()
    proto  = asyncio.StreamReaderProtocol(reader)
    pipe_r_file = open(pipe_r, 'rb', buffering=0)
    await loop.connect_read_pipe(lambda: proto, pipe_r_file)

    session = _StdioSession(reader)
    await fn(session, username)


def main():
    # Ensure the anetbbs package is importable regardless of PYTHONPATH.
    # This file lives in <install_root>/anetbbs/games/builtin_runner.py.
    _here         = os.path.dirname(os.path.abspath(__file__))   # .../anetbbs/games
    _pkg_root     = os.path.dirname(_here)                        # .../anetbbs
    _install_root = os.path.dirname(_pkg_root)                    # project root
    if _install_root not in sys.path:
        sys.path.insert(0, _install_root)

    if len(sys.argv) < 2 or not sys.argv[1]:
        sys.stderr.write('builtin_runner: no module path given\n')
        sys.exit(1)

    module_path = sys.argv[1]
    if ':' in module_path:
        mod_name, func_name = module_path.rsplit(':', 1)
    else:
        mod_name, func_name = module_path, 'launch'

    import importlib
    try:
        mod = importlib.import_module(mod_name)
        fn  = getattr(mod, func_name)
    except Exception as exc:
        sys.stderr.write(f'builtin_runner: cannot load {module_path}: {exc}\n')
        sys.exit(1)

    username = os.environ.get('BBS_USERNAME', 'player')

    # Raw mode: single keypresses reach the game without waiting for Enter.
    old_tc = None
    try:
        old_tc = termios.tcgetattr(0)
        tty.setraw(0)
    except Exception:
        pass

    from flask import Flask
    from anetbbs.config import get_config
    from anetbbs.models import db
    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)

    try:
        asyncio.run(_run(fn, username, app))
    finally:
        if old_tc is not None:
            try:
                termios.tcsetattr(0, termios.TCSADRAIN, old_tc)
            except Exception:
                pass


if __name__ == '__main__':
    main()
