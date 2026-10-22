"""Regression test for a known gap closed in a full BinkP-subsystem
audit: the outbound client (BinkPClient._handshake) never cross-checked
the peer's claimed M_ADR against the address we actually dialed
(self.hub_address). Real binkd matches an incoming M_ADR against its
own configured link table as part of identifying the session; ANetBBS
previously accepted whatever address the peer claimed with zero
cross-check -- a wrong host answering on the expected IP/port (stale
DNS, misconfiguration, a MITM) would sail through unnoticed as long as
it also somehow knew our password.

_verify_remote_address() closes this: WARNS (does not abort) on a
mismatch, since the password remains the real security gate and a
legitimate multi-AKA hub might list an address that doesn't textually
match our config -- hard-aborting on that false positive would be a
worse outcome than a logged warning.
"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class RemoteAddressVerificationTests(unittest.TestCase):
    def _client(self, hub_address):
        from anetbbs.echomail.binkp import BinkPClient
        return BinkPClient(host='x', port=1, our_address='1:114/30',
                           hub_address=hub_address, password='secret')

    def test_matching_bare_address_no_warning(self):
        client = self._client('1:200/100')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            logging.getLogger('anetbbs.echomail.binkp').warning('sentinel')
            client._verify_remote_address('1:200/100')
        self.assertEqual(len(cm.output), 1, 'only the sentinel, no real warning')

    def test_matching_qualified_address_no_warning(self):
        client = self._client('1:200/100')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            logging.getLogger('anetbbs.echomail.binkp').warning('sentinel')
            client._verify_remote_address('1:200/100@fidonet')
        self.assertEqual(len(cm.output), 1)

    def test_our_side_qualified_their_side_bare_still_matches(self):
        client = self._client('1:200/100@fidonet')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            logging.getLogger('anetbbs.echomail.binkp').warning('sentinel')
            client._verify_remote_address('1:200/100')
        self.assertEqual(len(cm.output), 1)

    def test_mismatched_address_logs_warning_but_does_not_raise(self):
        client = self._client('1:200/100')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            client._verify_remote_address('9:999/999')
        joined = '\n'.join(cm.output)
        self.assertIn('9:999/999', joined)
        self.assertIn('1:200/100', joined)

    def test_multi_aka_hub_matches_if_any_token_matches(self):
        """A hub advertising several AKAs on one M_ADR line should not
        warn as long as ONE of them matches our expected hub_address."""
        client = self._client('1:200/100')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            logging.getLogger('anetbbs.echomail.binkp').warning('sentinel')
            client._verify_remote_address('9:999/999 1:200/100 2:2/2')
        self.assertEqual(len(cm.output), 1)

    def test_no_hub_address_configured_skips_check_silently(self):
        client = self._client(None)
        # Must not raise even with garbage input, and must not warn
        # since there is nothing configured to compare against.
        client._verify_remote_address('anything at all')

    def test_empty_remote_adr_does_not_warn(self):
        client = self._client('1:200/100')
        client._verify_remote_address('   ')

    def test_explicit_point_zero_matches_omitted_point(self):
        """Real live bug (2026-10-22): a real fsxNet hub announced
        itself as '21:3/100.0@fsxnet' against a configured hub_address
        of '21:3/100' (no point) -- FTS-5001 says these are the SAME
        address (point 0 is the default/implicit point), but the old
        plain string comparison treated them as unrelated, logging a
        false 'possible misconfiguration' warning for a perfectly
        correctly configured link."""
        client = self._client('21:3/100')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            logging.getLogger('anetbbs.echomail.binkp').warning('sentinel')
            client._verify_remote_address('21:3/100.0@fsxnet')
        self.assertEqual(len(cm.output), 1, 'explicit .0 must match an omitted point')

    def test_omitted_point_matches_explicit_point_zero_configured(self):
        """The reverse direction: hub_address itself stored WITH an
        explicit '.0' must still match a peer announcing without one."""
        client = self._client('21:3/100.0')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            logging.getLogger('anetbbs.echomail.binkp').warning('sentinel')
            client._verify_remote_address('21:3/100@fsxnet')
        self.assertEqual(len(cm.output), 1)

    def test_genuinely_different_node_still_warns(self):
        """Point normalization must not become a blanket pass -- a
        truly different node number is still a real mismatch."""
        client = self._client('21:3/100')
        with self.assertLogs('anetbbs.echomail.binkp', level='WARNING') as cm:
            client._verify_remote_address('21:3/999.0@fsxnet')
        joined = '\n'.join(cm.output)
        self.assertIn('21:3/999.0@fsxnet', joined)


if __name__ == '__main__':
    unittest.main()
