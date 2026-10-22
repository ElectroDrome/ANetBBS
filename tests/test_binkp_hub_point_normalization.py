"""Regression test for a real live bug (2026-10-22): a real fsxNet hub
(zone:net 21:3, same net as the sysop's own address) was repeatedly
calling in to deliver echomail and getting rejected as an "unrecognized
peer" -- every single attempt, confirmed via the live binkp journal and
an empty BadAreaLog (ruling out the usual unknown-area/unsubscribed
drop reasons). Root cause traced to _handle_connection()'s candidate-
address matching (anetbbs/echomail/binkp_server.py): the hub announced
itself as "21:3/100.0@fsxnet" (an explicit point-0 suffix) while the
sysop's own configured EchomailNetwork.hub_address was stored as
"21:3/100" (point omitted) -- FTS-5001 says these are the exact same
address, and this project's own routing.format_address() already
encodes "never render point 0" as the canonical convention, but the
candidate-building code only ever stripped the "@domain" qualifier, not
this point-notation difference. The two strings never matched, so the
connection fell through to the unmatched/anonymous branch -- which
(correctly, for genuinely-anonymous callers) accepts netmail but drops
all echomail -- silently eating every message in that hub's packets,
oneliners included.

Reuses test_binkp_multi_hub_identity.py's harness (same pattern already
established by test_binkp_server_inbound_ftn_domain.py) rather than
building a new one.

The differentiator used below: ONLY a genuinely-matched upstream
EchomailNetwork enforces its own binkp_password (see _handle_
connection()'s 3a branch) -- the unmatched/anonymous fallback accepts
ANY password (real FTN nodelist-compliance behavior for unlisted
crashmail callers, see DownstreamNodeAuthCharacterizationTests in
test_binkp_multi_hub_identity.py). So a WRONG password returning
CMD_ERR (not CMD_OK) is direct proof the address candidate matching
actually found and used the right EchomailNetwork row -- if the point-
normalization fix were broken, the session would silently fall through
to the anonymous branch instead, which never checks the password at
all and would return CMD_OK regardless.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_binkp_multi_hub_identity import (
    _BinkpHandlerHarness, _FakeEchomailNetwork, _decode_sent_commands,
)
from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR


class HubAddressPointNormalizationTests(unittest.TestCase, _BinkpHandlerHarness):

    def test_explicit_point_zero_peer_matches_omitted_point_hub_address(self):
        """The real live case: hub_address stored without a point,
        peer announces with an explicit '.0'."""
        net = _FakeEchomailNetwork(id=11, hub_address='21:3/100',
                                   network_type='binkp', binkp_password='hubpw')
        writer, _ = self._run(networks=[net], nodes=[],
                              remote_addr='21:3/100.0@fsxnet', remote_pwd='hubpw')
        commands = [c for c, _ in _decode_sent_commands(writer.sent)]
        self.assertIn(CMD_OK, commands)
        self.assertNotIn(CMD_ERR, commands)

    def test_wrong_password_with_explicit_point_still_rejected_not_silently_anonymous(self):
        """Proves the match actually happened (not a silent fall-
        through to the no-password-check anonymous branch): a WRONG
        password against the point-mismatched address must still
        produce CMD_ERR, exactly as it would for an exact string match."""
        net = _FakeEchomailNetwork(id=11, hub_address='21:3/100',
                                   network_type='binkp', binkp_password='hubpw')
        writer, _ = self._run(networks=[net], nodes=[],
                              remote_addr='21:3/100.0@fsxnet', remote_pwd='WRONG')
        commands = [c for c, _ in _decode_sent_commands(writer.sent)]
        self.assertIn(CMD_ERR, commands)
        self.assertNotIn(CMD_OK, commands)

    def test_hub_address_stored_with_explicit_point_matches_omitted_point_peer(self):
        """The reverse direction: hub_address itself stored WITH an
        explicit '.0' must still match a peer announcing without one."""
        net = _FakeEchomailNetwork(id=11, hub_address='21:3/100.0',
                                   network_type='binkp', binkp_password='hubpw')
        writer, _ = self._run(networks=[net], nodes=[],
                              remote_addr='21:3/100@fsxnet', remote_pwd='hubpw')
        commands = [c for c, _ in _decode_sent_commands(writer.sent)]
        self.assertIn(CMD_OK, commands)
        self.assertNotIn(CMD_ERR, commands)

    def test_reverse_direction_wrong_password_also_rejected_not_silently_anonymous(self):
        """Same proof as the other wrong-password test above, for the
        reverse direction (hub_address stored WITH the point, peer
        announces without it) -- a correct-password test alone can't
        tell a real match apart from a lucky fall-through to the
        no-password-check anonymous branch; only a wrong password can."""
        net = _FakeEchomailNetwork(id=11, hub_address='21:3/100.0',
                                   network_type='binkp', binkp_password='hubpw')
        writer, _ = self._run(networks=[net], nodes=[],
                              remote_addr='21:3/100@fsxnet', remote_pwd='WRONG')
        commands = [c for c, _ in _decode_sent_commands(writer.sent)]
        self.assertIn(CMD_ERR, commands)
        self.assertNotIn(CMD_OK, commands)

    def test_genuinely_different_node_number_does_not_falsely_match(self):
        """Point normalization must not become a blanket pass -- a
        truly different node number must still fall through to the
        anonymous branch (CMD_OK with no password check at all, since
        that's real FTN nodelist-compliant behavior for an unlisted
        caller -- NOT a password-checked match against this network)."""
        net = _FakeEchomailNetwork(id=11, hub_address='21:3/100',
                                   network_type='binkp', binkp_password='hubpw')
        writer, _ = self._run(networks=[net], nodes=[],
                              remote_addr='21:3/999.0@fsxnet', remote_pwd='WRONG')
        commands = [c for c, _ in _decode_sent_commands(writer.sent)]
        self.assertIn(CMD_OK, commands,
                     'a genuinely different node must fall through to the '
                     'anonymous/crashmail branch, not be rejected outright')
        self.assertNotIn(CMD_ERR, commands)


if __name__ == '__main__':
    unittest.main()
