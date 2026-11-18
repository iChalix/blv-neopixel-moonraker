"""Auswahl und Vertrag der beiden Protokollvarianten."""

import unittest

from blv_neopixel import protocol as protocol_module
from blv_neopixel.config import Config, ConfigError, default_heaters
from blv_neopixel.state import Heater, HeaterState, PrinterState, PrinterStatus


def sample_state():
    return PrinterState(
        status=PrinterStatus.PRINTING,
        heaters={
            0: Heater(index=0, current=59.8, active=60.0, state=HeaterState.ACTIVE),
            1: Heater(index=1, current=234.5, active=235.0, state=HeaterState.ACTIVE),
        },
        progress=0.42,
    )


class NormalizeTest(unittest.TestCase):
    def test_aliases(self):
        for raw in ("3", "3.0", "V3", "v3.0", "alt", "old"):
            self.assertEqual(protocol_module.normalize(raw), protocol_module.V30, raw)
        for raw in ("4", "4.0", "4.1", "V4.1", "v4", "neu", "new"):
            self.assertEqual(protocol_module.normalize(raw), protocol_module.V41, raw)

    def test_whitespace_and_case(self):
        self.assertEqual(protocol_module.normalize("  V4.1  "), protocol_module.V41)

    def test_unknown(self):
        with self.assertRaises(protocol_module.UnknownVersion):
            protocol_module.normalize("2.0")


class ContractTest(unittest.TestCase):
    def test_both_protocols_build_valid_frames(self):
        for protocol in protocol_module.all_protocols():
            frame = protocol.build_frame(sample_state())
            self.assertTrue(frame.endswith("\n"), protocol.version)
            self.assertLessEqual(len(frame) - 1, protocol.max_frame_bytes, protocol.version)
            protocol.validate_frame(frame)

    def test_both_protocols_parse_their_own_frames(self):
        for protocol in protocol_module.all_protocols():
            printer = protocol.parse(protocol.build_frame(sample_state()))
            self.assertEqual(printer.status, "P", protocol.version)
            self.assertAlmostEqual(printer.heater_act_temp[0], 59.8, places=3)
            self.assertAlmostEqual(printer.fraction_printed, 0.42, places=2)
            self.assertEqual(printer.heater_status[1], 2)

    def test_frames_differ_between_versions(self):
        v30 = protocol_module.get("3.0").build_frame(sample_state())
        v41 = protocol_module.get("4.1").build_frame(sample_state())
        self.assertIn('"status":"P"', v30)
        self.assertIn('"status":"processing"', v41)
        self.assertNotEqual(v30, v41)

    def test_only_v4_polls(self):
        self.assertFalse(protocol_module.get("3.0").polls)
        self.assertTrue(protocol_module.get("4.1").polls)

    def test_request_detection(self):
        v41 = protocol_module.get("4.1")
        self.assertTrue(v41.is_request('M409 F"d99f"'))
        self.assertTrue(v41.is_request("  m409 f\"d99f\"  \r"))
        self.assertFalse(v41.is_request("irgendeine Debugzeile"))
        self.assertFalse(protocol_module.get("3.0").is_request('M409 F"d99f"'))

    def test_full_ring_support(self):
        self.assertTrue(protocol_module.get("3.0").supports_full_ring)
        self.assertFalse(protocol_module.get("4.1").supports_full_ring)

    def test_frame_errors_are_specific(self):
        for protocol in protocol_module.all_protocols():
            with self.assertRaises(protocol.frame_error):
                protocol.validate_frame("{kaputt}")


class ConfigIntegrationTest(unittest.TestCase):
    def test_default_is_v41(self):
        cfg = Config(heaters=default_heaters())
        self.assertEqual(cfg.printer.sketch_version, protocol_module.V41)
        self.assertIs(cfg.protocol, protocol_module.get("4.1"))
        self.assertTrue(cfg.protocol.polls)
        self.assertIn("V4", cfg.describe())

    def test_switching_version(self):
        cfg = Config(heaters=default_heaters())
        cfg.printer.sketch_version = protocol_module.V30
        self.assertFalse(cfg.protocol.polls)
        self.assertIn("V3.0", cfg.describe())

    def test_full_ring_warning_only_when_set_explicitly(self):
        cfg = Config(heaters=default_heaters())
        cfg.serial.port = "/dev/null"
        # Standardwert bei V4.x -> kein Hinweis, sonst stuende er bei jeder
        # Standardinstallation im Log
        self.assertEqual(cfg.warnings(), [])

        cfg.printer.full_ring_explicit = True
        notes = cfg.warnings()
        self.assertEqual(len(notes), 1)
        self.assertIn("full_ring_at_100", notes[0])

    def test_no_warning_for_v3(self):
        cfg = Config(heaters=default_heaters())
        cfg.printer.sketch_version = protocol_module.V30
        cfg.printer.full_ring_explicit = True
        cfg.serial.port = "/dev/null"
        self.assertEqual(cfg.warnings(), [])


if __name__ == "__main__":
    unittest.main()
