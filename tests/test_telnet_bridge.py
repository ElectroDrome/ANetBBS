"""Tests for the outbound telnet door bridge (anetbbs/games/telnet_bridge.py),
added 2026-07-04 for the door_telnet game type (external telnet-only game
servers like TWGS -- Trade Wars Game Server).

TelnetIACFilter is the novel piece here -- a small RFC 854 option
negotiation filter so a "dumb" telnet client doesn't leave the remote
server hanging waiting for negotiated features we don't implement, and
doesn't show negotiation control bytes to the user. TelnetConnection is
tested against a real local TCP server (no mocking of socket behavior)
since it's a thin, mostly-I/O wrapper -- the risk is in the IAC parsing,
not the socket plumbing, hence the split in testing depth.

No dedicated tests exist for the door_rlogin equivalents this module
was modeled on (rlogin_bridge.py / door_runner.py's launch_rlogin_session
/ play_rlogin_telnet) -- matched that same scope here rather than
inventing full Flask/DB integration tests for launch_telnet_session /
play_telnet_terminal, which have the same heavy app-context/DB
dependencies as their rlogin siblings.
"""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.games.telnet_bridge import TelnetIACFilter, TelnetConnection, \
    _IAC, _WILL, _WONT, _DO, _DONT, _SB, _SE


class TelnetIACFilterTests(unittest.TestCase):
    def test_plain_data_passes_through_unchanged(self):
        f = TelnetIACFilter()
        display, reply = f.process(b'hello world\r\n')
        self.assertEqual(display, b'hello world\r\n')
        self.assertEqual(reply, b'')

    def test_will_option_is_stripped_and_refused_with_dont(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _WILL, 1]))  # WILL ECHO
        self.assertEqual(display, b'')
        self.assertEqual(reply, bytes([_IAC, _DONT, 1]))

    def test_do_option_is_stripped_and_refused_with_wont(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _DO, 24]))  # DO TTYPE
        self.assertEqual(display, b'')
        self.assertEqual(reply, bytes([_IAC, _WONT, 24]))

    def test_wont_and_dont_need_no_reply(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _WONT, 1]))
        self.assertEqual(display, b'')
        self.assertEqual(reply, b'')
        display, reply = f.process(bytes([_IAC, _DONT, 1]))
        self.assertEqual(display, b'')
        self.assertEqual(reply, b'')

    def test_escaped_iac_iac_becomes_a_literal_0xff_byte(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _IAC]))
        self.assertEqual(display, b'\xff')
        self.assertEqual(reply, b'')

    def test_subnegotiation_block_is_fully_discarded(self):
        f = TelnetIACFilter()
        seq = bytes([_IAC, _SB, 24, 1]) + b'IGNOREME' + bytes([_IAC, _SE])
        display, reply = f.process(seq)
        self.assertEqual(display, b'')
        self.assertEqual(reply, b'')

    def test_mixed_text_and_negotiation_interleaved(self):
        f = TelnetIACFilter()
        data = b'Welcome ' + bytes([_IAC, _WILL, 1]) + b'to TWGS!'
        display, reply = f.process(data)
        self.assertEqual(display, b'Welcome to TWGS!')
        self.assertEqual(reply, bytes([_IAC, _DONT, 1]))

    def test_iac_sequence_split_across_two_calls_is_still_parsed(self):
        f = TelnetIACFilter()
        d1, r1 = f.process(bytes([_IAC]))
        d2, r2 = f.process(bytes([_WILL, 1]))
        self.assertEqual(d1 + d2, b'')
        self.assertEqual(r1 + r2, bytes([_IAC, _DONT, 1]))

    def test_negotiation_byte_itself_split_across_two_calls(self):
        f = TelnetIACFilter()
        d1, r1 = f.process(bytes([_IAC, _WILL]))
        d2, r2 = f.process(bytes([1]))
        self.assertEqual(d1 + d2, b'')
        self.assertEqual(r1 + r2, bytes([_IAC, _DONT, 1]))

    def test_multiple_negotiations_in_one_chunk_each_get_a_reply(self):
        f = TelnetIACFilter()
        data = (bytes([_IAC, _WILL, 1])
               + bytes([_IAC, _WILL, 3])
               + bytes([_IAC, _DO, 24]))
        display, reply = f.process(data)
        self.assertEqual(display, b'')
        self.assertEqual(reply,
                         bytes([_IAC, _DONT, 1])
                         + bytes([_IAC, _DONT, 3])
                         + bytes([_IAC, _WONT, 24]))


class TelnetIACFilterMudProtocolTests(unittest.TestCase):
    """Phase 3 of the door-games roadmap (2026-10-20): confirms the
    EXISTING blanket-refuse/discard-everything behavior in
    TelnetIACFilter already correctly handles the three real MUD
    telnet protocol extensions (MSSP=70, MCCP2=86, GMCP=201 -- same
    option numbers ANetBBS's own session.py now speaks server-side,
    see tests/test_mud_protocol_extensions.py) when DIALING OUT to an
    external MUD, with NO new production code needed:

      - An offered MSSP/MCCP2/GMCP gets refused (DONT/WONT) exactly
        like any other unsupported option -- a compliant remote never
        proceeds to send the follow-up data a `DO` would have
        unlocked (MSSP's variable dump, MCCP2's compression-start ack,
        GMCP's own messages).
      - Even if a non-compliant remote sent one of those anyway, ANY
        IAC SB ... IAC SE block is already unconditionally discarded
        regardless of its option byte -- never shown to the user.

    This is the real wire-format precedent check this plan's Phase 3
    called for: confirm current behavior with a real fixture test
    first, add new code only if testing shows the existing refusal
    insufficient. It's sufficient -- no telnet_bridge.py changes were
    needed for this phase."""

    _MSSP, _MCCP2, _GMCP = 70, 86, 201

    def test_mssp_offer_is_refused_like_any_other_option(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _WILL, self._MSSP]))
        self.assertEqual(display, b'')
        self.assertEqual(reply, bytes([_IAC, _DONT, self._MSSP]))

    def test_mccp2_offer_is_refused_like_any_other_option(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _WILL, self._MCCP2]))
        self.assertEqual(display, b'')
        self.assertEqual(reply, bytes([_IAC, _DONT, self._MCCP2]))

    def test_gmcp_offer_is_refused_like_any_other_option(self):
        f = TelnetIACFilter()
        display, reply = f.process(bytes([_IAC, _WILL, self._GMCP]))
        self.assertEqual(display, b'')
        self.assertEqual(reply, bytes([_IAC, _DONT, self._GMCP]))

    def test_a_noncompliant_mssp_dump_sent_anyway_is_still_fully_discarded(self):
        """Defense in depth: even if a remote ignored our DONT and sent
        the real IAC SB MSSP MSSP_VAR "NAME" MSSP_VAL "TestMUD" IAC SE
        payload anyway, it must never reach the display stream."""
        f = TelnetIACFilter()
        mssp_var, mssp_val = 1, 2
        payload = (bytes([mssp_var]) + b'NAME' + bytes([mssp_val]) + b'TestMUD')
        seq = bytes([_IAC, _SB, self._MSSP]) + payload + bytes([_IAC, _SE])
        display, reply = f.process(seq)
        self.assertEqual(display, b'')
        self.assertEqual(reply, b'')

    def test_a_noncompliant_gmcp_message_sent_anyway_is_still_fully_discarded(self):
        f = TelnetIACFilter()
        seq = (bytes([_IAC, _SB, self._GMCP]) + b'Char.Vitals {"hp": 10}'
              + bytes([_IAC, _SE]))
        display, reply = f.process(seq)
        self.assertEqual(display, b'')
        self.assertEqual(reply, b'')

    def test_real_mud_banner_with_mssp_mccp2_gmcp_offers_yields_clean_display(self):
        """End-to-end shape: a real MUD's connect banner typically
        offers several options up front, interleaved with plain text
        -- confirms the user-visible output is exactly the plain text,
        with every option offer correctly refused."""
        f = TelnetIACFilter()
        data = (bytes([_IAC, _WILL, self._MSSP])
               + bytes([_IAC, _WILL, self._MCCP2])
               + bytes([_IAC, _WILL, self._GMCP])
               + b'\r\nWelcome to TestMUD!\r\n')
        display, reply = f.process(data)
        self.assertEqual(display, b'\r\nWelcome to TestMUD!\r\n')
        self.assertEqual(reply,
                         bytes([_IAC, _DONT, self._MSSP])
                         + bytes([_IAC, _DONT, self._MCCP2])
                         + bytes([_IAC, _DONT, self._GMCP]))


class _EchoNegotiatingServer:
    """Minimal local TCP server: sends one WILL ECHO negotiation on
    connect, then echoes back anything it receives. Used to exercise
    TelnetConnection against a real socket without depending on any
    external network service."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received_from_client = bytearray()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        try:
            conn.sendall(bytes([_IAC, _WILL, 1]) + b'Welcome to TestServer\r\n')
            conn.settimeout(2)
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                self.received_from_client += data
                conn.sendall(data)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class TelnetConnectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = _EchoNegotiatingServer()
        self.addCleanup(self.server.close)

    def test_connect_and_receive_filtered_banner(self):
        conn = TelnetConnection('127.0.0.1', self.server.port)
        conn.connect()
        self.addCleanup(conn.stop)

        received = []
        done = threading.Event()

        def _emit(data):
            received.append(data)
            if b'Welcome' in data:
                done.set()

        conn.bind_emit(_emit, idle_timeout=5)
        self.assertTrue(done.wait(3), 'never received the banner')
        joined = b''.join(received)
        # The WILL ECHO negotiation must be stripped -- no raw IAC byte
        # should ever reach the "user".
        self.assertNotIn(bytes([_IAC]), joined)
        self.assertIn(b'Welcome to TestServer', joined)

    def test_write_reaches_the_server(self):
        conn = TelnetConnection('127.0.0.1', self.server.port)
        conn.connect()
        self.addCleanup(conn.stop)
        conn.bind_emit(lambda data: None, idle_timeout=5)
        conn.write(b'hello\r\n')
        # Give the server's thread a moment to receive it.
        for _ in range(20):
            if b'hello' in self.server.received_from_client:
                break
            time.sleep(0.1)
        self.assertIn(b'hello', bytes(self.server.received_from_client))

    def test_connect_refused_raises_oserror(self):
        # Nothing listening on this port (server not started there).
        conn = TelnetConnection('127.0.0.1', 1)
        with self.assertRaises(OSError):
            conn.connect(timeout=2)


if __name__ == '__main__':
    unittest.main()
