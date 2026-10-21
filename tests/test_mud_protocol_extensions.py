"""Regression tests for MUD telnet protocol extensions (Phase 2 of the
door-games roadmap, 2026-10-20): MSSP, MCCP2, GMCP.

Real wire-format specs pulled directly from tintin.mudhalla.net (the
canonical MUD-protocol reference) this session, not guessed:
  - MSSP (option 70): server sends IAC WILL MSSP; on IAC DO MSSP
    reply, server sends IAC SB MSSP MSSP_VAR "name" MSSP_VAL "value"
    ... IAC SE (MSSP_VAR=0x01, MSSP_VAL=0x02).
  - MCCP2 (option 86): server sends IAC WILL MCCP2; on IAC DO MCCP2,
    server sends IAC SB MCCP2 IAC SE UNCOMPRESSED, then a zlib (RFC
    1950) stream begins on ALL subsequent output.
  - GMCP (option 201): server sends IAC WILL GMCP; on IAC DO GMCP,
    either side may send IAC SB GMCP <package.cmd> <json> IAC SE.

This is foundational groundwork for a future native MUD door (no such
door exists yet) -- these tests drive the mechanism directly via
BBSSession.mud_protocol_negotiate_start()/_stop() and
handle_telnet_command(), the same way a door would, rather than
requiring an actual MUD to exist.
"""
import asyncio
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession, IAC, WILL, DO, SB, SE, MSSP, MCCP2, GMCP


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True


def _make_session(term_mode='ansi'):
    # term_mode is a read-only property computed from
    # self._forced_term_mode (falling back to NAWS/TTYPE heuristics) --
    # set the backing attribute, not the property itself.
    session = BBSSession(reader=None, writer=_FakeWriter(), config={})
    session._forced_term_mode = term_mode
    return session


class MssvOfferAndReplyTests(unittest.TestCase):
    def test_negotiate_start_offers_all_three_options(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        out = bytes(session.writer.written)
        self.assertIn(IAC + WILL + MSSP, out)
        self.assertIn(IAC + WILL + MCCP2, out)
        self.assertIn(IAC + WILL + GMCP, out)

    def test_negotiate_start_is_a_noop_for_enhanced_term_mode(self):
        session = _make_session(term_mode='enhanced')
        asyncio.run(session.mud_protocol_negotiate_start())
        self.assertEqual(bytes(session.writer.written), b'')
        self.assertFalse(session._mud_protocol_mode)

    def test_negotiate_start_is_a_noop_for_petscii_term_mode(self):
        session = _make_session(term_mode='petscii')
        asyncio.run(session.mud_protocol_negotiate_start())
        self.assertEqual(bytes(session.writer.written), b'')
        self.assertFalse(session._mud_protocol_mode)

    def test_client_do_mssp_triggers_the_real_wire_format_reply(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start(
            mssp_vars={'NAME': 'TestMUD', 'PLAYERS': '3', 'UPTIME': '12345'}))
        session.writer.written.clear()
        asyncio.run(session.handle_telnet_command(IAC + DO + MSSP))
        out = bytes(session.writer.written)
        self.assertTrue(out.startswith(IAC + SB + MSSP))
        self.assertTrue(out.endswith(IAC + SE))
        MSSP_VAR, MSSP_VAL = bytes([1]), bytes([2])
        self.assertIn(MSSP_VAR + b'NAME' + MSSP_VAL + b'TestMUD', out)
        self.assertIn(MSSP_VAR + b'PLAYERS' + MSSP_VAL + b'3', out)
        self.assertIn(MSSP_VAR + b'UPTIME' + MSSP_VAL + b'12345', out)

    def test_mssp_reply_has_generic_fallbacks_when_door_sets_nothing(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        session.writer.written.clear()
        asyncio.run(session.handle_telnet_command(IAC + DO + MSSP))
        out = bytes(session.writer.written)
        MSSP_VAR = bytes([1])
        for required in (b'NAME', b'PLAYERS', b'UPTIME'):
            self.assertIn(MSSP_VAR + required, out)

    def test_do_mssp_without_negotiate_start_is_ignored(self):
        """A plain BBS session (never called mud_protocol_negotiate_
        start()) must not react to a stray IAC DO MSSP -- confirms the
        feature is fully opt-in, not globally active."""
        session = _make_session()
        asyncio.run(session.handle_telnet_command(IAC + DO + MSSP))
        self.assertEqual(bytes(session.writer.written), b'')


class Mccp2CompressionTests(unittest.TestCase):
    def test_do_mccp2_sends_uncompressed_ack_then_creates_compressor(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        session.writer.written.clear()
        asyncio.run(session.handle_telnet_command(IAC + DO + MCCP2))
        self.assertEqual(bytes(session.writer.written), IAC + SB + MCCP2 + IAC + SE)
        self.assertIsNotNone(session._mccp2_compressor)

    def test_output_before_mccp2_negotiated_is_plain_text(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        session.writer.written.clear()
        asyncio.run(session.write('hello world'))
        self.assertEqual(bytes(session.writer.written), b'hello world')

    def test_output_after_mccp2_negotiated_is_a_real_zlib_stream(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        asyncio.run(session.handle_telnet_command(IAC + DO + MCCP2))
        session.writer.written.clear()
        asyncio.run(session.write('hello world'))
        compressed = bytes(session.writer.written)
        self.assertNotEqual(compressed, b'hello world')
        decompressor = zlib.decompressobj()
        self.assertEqual(decompressor.decompress(compressed), b'hello world')

    def test_multiple_writes_form_one_continuous_decompressible_stream(self):
        """Z_SYNC_FLUSH per write() call must still produce a single
        valid zlib stream when all the compressed chunks are fed to
        ONE decompressor in order -- not independently-valid streams
        per call."""
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        asyncio.run(session.handle_telnet_command(IAC + DO + MCCP2))
        session.writer.written.clear()
        asyncio.run(session.write('first '))
        asyncio.run(session.write('second'))
        compressed = bytes(session.writer.written)
        decompressor = zlib.decompressobj()
        self.assertEqual(decompressor.decompress(compressed), b'first second')

    def test_negotiate_stop_flushes_and_ends_compression(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        asyncio.run(session.handle_telnet_command(IAC + DO + MCCP2))
        asyncio.run(session.mud_protocol_negotiate_stop())
        self.assertIsNone(session._mccp2_compressor)
        session.writer.written.clear()
        asyncio.run(session.write('back to plain text'))
        self.assertEqual(bytes(session.writer.written), b'back to plain text')


class GmcpTests(unittest.TestCase):
    def test_do_gmcp_enables_the_flag(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        asyncio.run(session.handle_telnet_command(IAC + DO + GMCP))
        self.assertTrue(session._mud_gmcp_enabled)

    def test_send_gmcp_before_client_accepts_is_a_noop(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        session.writer.written.clear()
        asyncio.run(session.send_gmcp('Char.Vitals', {'hp': 10}))
        self.assertEqual(bytes(session.writer.written), b'')

    def test_send_gmcp_real_wire_framing(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        asyncio.run(session.handle_telnet_command(IAC + DO + GMCP))
        session.writer.written.clear()
        asyncio.run(session.send_gmcp('Char.Vitals', {'hp': 10}))
        out = bytes(session.writer.written)
        self.assertEqual(out, IAC + SB + GMCP + b'Char.Vitals {"hp": 10}' + IAC + SE)

    def test_send_gmcp_with_no_data_omits_the_space_and_payload(self):
        session = _make_session()
        asyncio.run(session.mud_protocol_negotiate_start())
        asyncio.run(session.handle_telnet_command(IAC + DO + GMCP))
        session.writer.written.clear()
        asyncio.run(session.send_gmcp('Core.Ping'))
        out = bytes(session.writer.written)
        self.assertEqual(out, IAC + SB + GMCP + b'Core.Ping' + IAC + SE)


class NonMudSessionUnaffectedTests(unittest.TestCase):
    """A plain BBS session browsing boards/mail must see none of this
    -- the whole mechanism is opt-in per the door that calls
    mud_protocol_negotiate_start(), never globally active."""

    def test_ordinary_write_is_never_compressed(self):
        session = _make_session()
        asyncio.run(session.write('ordinary BBS text'))
        self.assertEqual(bytes(session.writer.written), b'ordinary BBS text')

    def test_send_gmcp_is_a_noop_without_negotiate_start(self):
        session = _make_session()
        asyncio.run(session.send_gmcp('Char.Vitals', {'hp': 10}))
        self.assertEqual(bytes(session.writer.written), b'')


if __name__ == '__main__':
    unittest.main()
