"""Source-inspection regression test for the door_runner.py half of the
BBS_USER_ID fix -- see test_builtin_runner_stdio_session_user_id.py's
own docstring for the full root-cause writeup.

launch_door_game() isn't directly unit-tested anywhere in this codebase
with a real fork (too heavy/fragile -- see
tests/test_door_launch_post_fork_commit_resilience.py's own docstring
for the established precedent this test follows): the env-var-setting
code lives inside the `if pid == 0:` post-fork child branch. This
confirms BBS_USER_ID is set from the same _ufield() helper every other
BBS_* env var already uses (so it works for both a dict-shaped user
(telnet/SSH/rlogin) and a real User model (web's current_user)), right
alongside BBS_USERNAME.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DoorRunnerBbsUserIdEnvTests(unittest.TestCase):
    def test_bbs_user_id_env_var_is_set_from_ufield(self):
        from anetbbs.games.door_runner import launch_door_game
        source = inspect.getsource(launch_door_game)
        self.assertIn("os.environ['BBS_USER_ID']", source)
        idx = source.index("os.environ['BBS_USER_ID']")
        line = source[idx:source.index('\n', idx)]
        self.assertIn("_ufield('id'", line)

    def test_bbs_user_id_is_set_alongside_bbs_username(self):
        """Must be set in the same env-populating block as the other
        BBS_* vars, not some unrelated/later code path that might not
        run for every game type."""
        from anetbbs.games.door_runner import launch_door_game
        source = inspect.getsource(launch_door_game)
        username_idx = source.index("os.environ['BBS_USERNAME']")
        user_id_idx = source.index("os.environ['BBS_USER_ID']")
        # Generous window -- just confirms they're neighbors, not that
        # one is immediately after the other character-for-character.
        self.assertLess(abs(user_id_idx - username_idx), 200)


if __name__ == '__main__':
    unittest.main()
