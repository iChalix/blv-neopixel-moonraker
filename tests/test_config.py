"""Konfiguration laden und pruefen."""

import os
import tempfile
import unittest

from blv_neopixel import config as config_module
from blv_neopixel.config import ConfigError
from blv_neopixel.state import PrinterStatus

SAMPLE = """
[moonraker]
url = http://192.168.1.50:7125

[serial]
port = /dev/serial/by-id/usb-arduino-0
baudrate = 57600
avoid_reset = false
reset_delay = 1.5

[printer]
sketch_version = V4.1
progress_source = layer
interval_active = 0.5
interval_idle = 4
min_interval = 0.4
complete_hold = 15
offline_status = C
error_status = stopped

[heater 0]
object = heater_bed
name = Bett

[heater 1]
object = extruder
name = Hotend

[heater 2]
object = heater_generic chamber

[logging]
level = debug
"""


def write_config(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def load(self, text):
        path = write_config(text)
        self.paths.append(path)
        return config_module.load(path)

    def test_full_sample(self):
        cfg = self.load(SAMPLE)
        self.assertEqual(cfg.moonraker.url, "ws://192.168.1.50:7125/websocket")
        self.assertEqual(cfg.serial.port, "/dev/serial/by-id/usb-arduino-0")
        self.assertFalse(cfg.serial.avoid_reset)
        self.assertAlmostEqual(cfg.serial.reset_delay, 1.5)
        self.assertEqual(cfg.printer.sketch_version, "4.1")
        self.assertTrue(cfg.protocol.polls)
        self.assertEqual(cfg.printer.progress_source, "layer")
        self.assertAlmostEqual(cfg.printer.interval_active, 0.5)
        self.assertEqual(cfg.printer.offline_status, PrinterStatus.CONFIGURING)
        self.assertEqual(cfg.printer.error_status, PrinterStatus.STOPPED)
        self.assertEqual(cfg.log_level, "debug")
        self.assertEqual([h.index for h in cfg.heaters], [0, 1, 2])
        self.assertEqual(cfg.heaters[2].object, "heater_generic chamber")
        self.assertEqual(cfg.heaters[2].label, "heater_generic chamber")

    def test_defaults_without_file(self):
        cfg = config_module.Config(heaters=config_module.default_heaters())
        self.assertEqual(cfg.moonraker.url, "ws://127.0.0.1:7125/websocket")
        self.assertEqual(cfg.serial.baudrate, 57600)
        self.assertEqual([h.object for h in cfg.heaters], ["heater_bed", "extruder"])

    def test_missing_file_raises(self):
        with self.assertRaises(ConfigError):
            config_module.load("/definitiv/nicht/vorhanden.conf")

    def test_url_variants(self):
        for given, expected in (
            ("127.0.0.1:7125", "ws://127.0.0.1:7125/websocket"),
            ("http://pi:7125", "ws://pi:7125/websocket"),
            ("https://pi:7125", "wss://pi:7125/websocket"),
            ("ws://pi:7125/websocket", "ws://pi:7125/websocket"),
        ):
            cfg = self.load("[moonraker]\nurl = %s\n" % given)
            self.assertEqual(cfg.moonraker.url, expected, given)

    def test_api_key_file(self):
        key_path = write_config("abc123\n")
        self.paths.append(key_path)
        cfg = self.load("[moonraker]\napi_key_file = %s\n" % key_path)
        self.assertEqual(cfg.moonraker.api_key, "abc123")

    def test_duplicate_heater_index(self):
        with self.assertRaises(ConfigError):
            self.load("[heater 0]\nobject = a\n\n[heater 00]\nobject = b\n")

    def test_heater_index_out_of_range(self):
        with self.assertRaises(ConfigError):
            self.load("[heater 5]\nobject = a\n")

    def test_heater_without_object(self):
        with self.assertRaises(ConfigError):
            self.load("[heater 0]\nname = Bett\n")

    def test_bad_section_name(self):
        with self.assertRaises(ConfigError):
            self.load("[heater]\nobject = a\n")

    def test_sketch_version_defaults_to_v41(self):
        cfg = self.load("[serial]\nport = /dev/ttyUSB0\n")
        self.assertEqual(cfg.printer.sketch_version, "4.1")

    def test_full_ring_is_only_explicit_when_written(self):
        cfg = self.load("[printer]\nsketch_version = 3.0\n")
        self.assertTrue(cfg.printer.full_ring_at_100)
        self.assertFalse(cfg.printer.full_ring_explicit)

        cfg = self.load("[printer]\nfull_ring_at_100 = false\n")
        self.assertFalse(cfg.printer.full_ring_at_100)
        self.assertTrue(cfg.printer.full_ring_explicit)

    def test_sketch_version_aliases(self):
        for given, expected in (("3", "3.0"), ("V3.0", "3.0"), ("alt", "3.0"), ("4", "4.1"), ("neu", "4.1")):
            cfg = self.load("[printer]\nsketch_version = %s\n" % given)
            self.assertEqual(cfg.printer.sketch_version, expected, given)

    def test_unknown_sketch_version(self):
        with self.assertRaises(ConfigError):
            self.load("[printer]\nsketch_version = 2.0\n")

    def test_unknown_progress_source(self):
        with self.assertRaises(ConfigError):
            self.load("[printer]\nprogress_source = magie\n")

    def test_unknown_status(self):
        with self.assertRaises(ConfigError):
            self.load("[printer]\noffline_status = Q\n")

    def test_intervals_must_respect_min_interval(self):
        with self.assertRaises(ConfigError):
            self.load("[printer]\nmin_interval = 1.0\ninterval_active = 0.5\n")

    def test_comments_are_ignored(self):
        cfg = self.load("[serial]\nport = /dev/ttyUSB0  # der Arduino\n")
        self.assertEqual(cfg.serial.port, "/dev/ttyUSB0")


class DescribeTest(unittest.TestCase):
    def test_describe_mentions_everything_important(self):
        cfg = config_module.Config(heaters=config_module.default_heaters())
        text = cfg.describe()
        self.assertIn("Moonraker", text)
        self.assertIn("heater_bed", text)
        self.assertIn("extruder", text)


if __name__ == "__main__":
    unittest.main()
