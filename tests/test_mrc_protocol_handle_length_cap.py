"""Regression test for a real Medium finding from a follow-up security
audit: mrc/bridge/mrc_protocol.py's validate_handle() had no length
cap at all. Any anonymous client (unregistered/casual handles are
intentionally allowed to chat freely, matching the real MRC network --
see main.py's own comments near /identify) can set an arbitrarily long
handle and have it persisted PERMANENTLY into profiles.json the first
time it calls set_style/set_prefs, with no pruning -- unbounded growth
of the on-disk/in-memory store, and every save gets progressively
slower as more profiles accumulate (profiles.json is a full-file
rewrite on every save), which also widens the concurrent-write race
window fixed separately in mrc/bridge/db.py.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.mrc_protocol import MRCProtocol


class HandleLengthCapTests(unittest.TestCase):
    def test_handle_at_the_cap_is_still_valid(self):
        handle = 'a' * MRCProtocol.MAX_HANDLE_LENGTH
        self.assertTrue(MRCProtocol.validate_handle(handle))

    def test_handle_one_over_the_cap_is_rejected(self):
        handle = 'a' * (MRCProtocol.MAX_HANDLE_LENGTH + 1)
        self.assertFalse(MRCProtocol.validate_handle(handle))

    def test_wildly_oversized_handle_is_rejected(self):
        handle = 'a' * 10000
        self.assertFalse(MRCProtocol.validate_handle(handle))

    def test_normal_short_handle_still_works(self):
        self.assertTrue(MRCProtocol.validate_handle('Wizop'))

    def test_empty_handle_still_rejected(self):
        self.assertFalse(MRCProtocol.validate_handle(''))

    def test_reserved_handle_still_rejected_regardless_of_length_check_order(self):
        # RESERVED_HANDLES check must still work now that the length
        # check runs first -- a short reserved name must still fail.
        reserved = next(iter(MRCProtocol.RESERVED_HANDLES))
        self.assertFalse(MRCProtocol.validate_handle(reserved))


if __name__ == '__main__':
    unittest.main()
