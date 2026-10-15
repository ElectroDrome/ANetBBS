"""Regression test for a real Medium finding from a follow-up security
audit: mrc/bridge/db.py's save_profile_async()/save_session_async()/
delete_session_async() offloaded their actual file write to the
default executor with NO synchronization at all. Two independent
handlers (e.g. two different users each triggering a profile or
session save around the same moment -- a near-certainty on a bridge
with more than a couple of simultaneous chatters over time) could both
open the SAME profiles.json.tmp/sessions.json.tmp path concurrently,
truncating/interleaving each other's bytes before whichever
os.replace() ran last published the result -- and on the next bridge
restart, _load_json() hits a JSONDecodeError on the corrupted file and
silently resets it to {}, wiping every registered user's profile or
every live session, bridge-wide, not just the colliding ones.

Per this project's own verify-by-reverting / stress-test discipline
for race-condition fixes (a prior flock-based fix elsewhere was still
~20% racy under a 30-thread stress test despite looking correct on
paper): this drives real concurrent writes through asyncio's actual
executor thread pool (not mocked), repeated across many runs, and
confirms the file is ALWAYS valid, parseable, complete JSON afterward
-- never corrupted, never missing an entry.
"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'mrc' / 'bridge'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.db import BridgeDB


class ConcurrentWriteRaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def test_many_concurrent_session_saves_never_corrupt_the_file(self):
        N = 40
        RUNS = 10  # repeat per this project's own race-condition stress
                   # discipline -- a single clean run proves nothing

        for run in range(RUNS):
            db = BridgeDB(data_dir=self._tmp)

            async def _drive():
                await asyncio.gather(*[
                    db.save_session_async(f'sess-{i}', {'handle': f'user{i}'})
                    for i in range(N)
                ])

            asyncio.run(_drive())

            # The file on disk must ALWAYS be valid, complete JSON --
            # never truncated/interleaved by a racing concurrent write.
            import json
            with open(db.sessions_file) as f:
                on_disk = json.load(f)  # raises if corrupted
            self.assertEqual(len(on_disk), N,
                             f'run {run}: expected {N} sessions on disk, '
                             f'got {len(on_disk)} -- a concurrent write race '
                             f'silently dropped/corrupted some entries')
            for i in range(N):
                self.assertIn(f'sess-{i}', on_disk)

    def test_many_concurrent_profile_saves_never_corrupt_the_file(self):
        N = 40
        RUNS = 10

        for run in range(RUNS):
            db = BridgeDB(data_dir=self._tmp)

            async def _drive():
                await asyncio.gather(*[
                    db.save_profile_async(f'handle{i}', {'style': f'style{i}'})
                    for i in range(N)
                ])

            asyncio.run(_drive())

            import json
            with open(db.profiles_file) as f:
                on_disk = json.load(f)
            self.assertEqual(len(on_disk), N,
                             f'run {run}: expected {N} profiles on disk, '
                             f'got {len(on_disk)}')

    def test_mixed_saves_and_deletes_never_corrupt_the_file(self):
        """A more realistic mixed workload -- some sessions being
        created, others being deleted, concurrently."""
        RUNS = 10

        for run in range(RUNS):
            db = BridgeDB(data_dir=self._tmp)

            # Seed and drive within the SAME asyncio.run() -- a real
            # bridge process lives inside one long-running event loop
            # for its whole lifetime (never crosses asyncio.run()
            # calls), and asyncio.Lock() binds to whichever loop first
            # uses it; splitting this into two separate asyncio.run()
            # calls against the same db/lock would raise "bound to a
            # different event loop", an artifact of an unrealistic
            # test shape, not a real production bug.
            async def _seed_then_drive():
                await asyncio.gather(*[
                    db.save_session_async(f'sess-{i}', {'handle': f'user{i}'})
                    for i in range(20)
                ])
                tasks = [db.save_session_async(f'sess-new-{i}', {'handle': f'new{i}'})
                        for i in range(10)]
                tasks += [db.delete_session_async(f'sess-{i}') for i in range(10)]
                await asyncio.gather(*tasks)
            asyncio.run(_seed_then_drive())

            import json
            with open(db.sessions_file) as f:
                on_disk = json.load(f)  # raises if corrupted
            # 20 seeded - 10 deleted + 10 new = 20
            self.assertEqual(len(on_disk), 20,
                             f'run {run}: mixed concurrent save/delete '
                             f'left {len(on_disk)} sessions, expected 20')


if __name__ == '__main__':
    unittest.main()
