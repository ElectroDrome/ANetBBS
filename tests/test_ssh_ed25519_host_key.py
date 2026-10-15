"""Regression tests for adding a second (Ed25519) SSH host key alongside
the existing RSA one.

Context: an SSH-precedent review (comparing ANetBBS against Synchronet/
Mystic/SyncTerm) found ANetBBS only ever generated/offered a single
RSA-2048 host key. Modern SSH servers commonly offer both an RSA key
(for older clients) and an Ed25519 key (smaller, faster, no SHA-1
legacy baggage) side by side -- which is also explicitly the pairing
Synchronet's own in-development SSH replacement library targets. These
tests confirm: the Ed25519 key is generated once and reused afterward
(not regenerated every start), it's a real Ed25519 key (not just a
differently-named RSA key), the existing RSA key path/behavior is
completely untouched, and start_ssh_server() offers BOTH keys to
asyncssh rather than replacing the RSA one.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncssh

from anetbbs.core import ssh_server


class Ed25519HostKeyGenerationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.rsa_key_file = os.path.join(self._tmpdir.name, 'ssh_host_key')

    def test_generates_ed25519_key_alongside_rsa_path(self):
        expected_path = self.rsa_key_file + '_ed25519'
        self.assertFalse(os.path.exists(expected_path))

        result_path = ssh_server._ensure_ed25519_host_key(self.rsa_key_file)

        self.assertEqual(result_path, os.path.abspath(expected_path))
        self.assertTrue(os.path.exists(expected_path))

    def test_generated_key_is_really_ed25519_not_rsa(self):
        key_path = ssh_server._ensure_ed25519_host_key(self.rsa_key_file)
        loaded = asyncssh.read_private_key(key_path)
        self.assertEqual(loaded.get_algorithm(), 'ssh-ed25519')

    def test_private_key_file_mode_is_restricted(self):
        key_path = ssh_server._ensure_ed25519_host_key(self.rsa_key_file)
        mode = os.stat(key_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_second_call_does_not_regenerate(self):
        first_path = ssh_server._ensure_ed25519_host_key(self.rsa_key_file)
        with open(first_path, 'rb') as f:
            original_bytes = f.read()

        second_path = ssh_server._ensure_ed25519_host_key(self.rsa_key_file)
        with open(second_path, 'rb') as f:
            second_bytes = f.read()

        self.assertEqual(first_path, second_path)
        self.assertEqual(original_bytes, second_bytes)

    def test_does_not_touch_or_require_the_rsa_key_file(self):
        # The RSA key doesn't even need to exist yet for the Ed25519
        # helper to do its own job -- the two are independent siblings.
        self.assertFalse(os.path.exists(self.rsa_key_file))
        ssh_server._ensure_ed25519_host_key(self.rsa_key_file)
        self.assertFalse(os.path.exists(self.rsa_key_file))


class StartSshServerOffersBothHostKeysTests(unittest.TestCase):
    def test_both_rsa_and_ed25519_paths_passed_to_asyncssh(self):
        async def _fake_create_server(*args, **kwargs):
            _fake_create_server.kwargs = kwargs
            return object()

        with mock.patch.object(ssh_server.asyncssh, 'create_server',
                               side_effect=_fake_create_server), \
             mock.patch.object(ssh_server, '_ensure_host_key',
                               return_value='/tmp/fake-rsa-key'), \
             mock.patch.object(ssh_server, '_ensure_ed25519_host_key',
                               return_value='/tmp/fake-ed25519-key'):
            asyncio.run(ssh_server.start_ssh_server(
                '0.0.0.0', 2234, '/tmp/unused-key-file', bbs_config={}))

        host_keys = _fake_create_server.kwargs['server_host_keys']
        self.assertEqual(host_keys, ['/tmp/fake-rsa-key', '/tmp/fake-ed25519-key'])


if __name__ == '__main__':
    unittest.main()
