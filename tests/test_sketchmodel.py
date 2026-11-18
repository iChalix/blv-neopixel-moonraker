"""Prueft die Rahmen gegen den nachgebauten V3.0-Sketch."""

import unittest

from blv_neopixel import duet, sketchmodel
from blv_neopixel.sketchmodel import RingConfig, STATUS_COLORS, DEFAULT_HEATER_COLORS
from blv_neopixel.state import Heater, HeaterState, PrinterState, PrinterStatus


def frame_for(status, progress=0.0, heaters=None, full_ring=True):
    return duet.build_frame(
        PrinterState(status=status, heaters=heaters or {}, progress=progress),
        full_ring=full_ring,
    )


class ParserQuirksTest(unittest.TestCase):
    def test_message_starts_at_brace_and_ends_at_first_brace(self):
        raw = 'Muell davor {"status":"I"} und danach {"status":"P"}'
        self.assertEqual(sketchmodel.get_serial_message(raw), "{status:I}")

    def test_quotes_are_dropped(self):
        self.assertEqual(sketchmodel.get_serial_message('{"a":"b"}'), "{a:b}")

    def test_single_element_array_is_unreadable_by_index(self):
        message = "{heaters:[42.0]}"
        self.assertIsNone(sketchmodel.json_parse_root(message, "heaters", 1))

    def test_two_element_array_is_readable(self):
        message = "{heaters:[42.0,13.0]}"
        self.assertEqual(sketchmodel.json_parse_root(message, "heaters", 1), "42.0")
        self.assertEqual(sketchmodel.json_parse_root(message, "heaters", 2), "13.0")
        self.assertIsNone(sketchmodel.json_parse_root(message, "heaters", 3))

    def test_scalar_before_closing_brace(self):
        message = "{fraction_printed:0.5}"
        self.assertEqual(sketchmodel.json_parse_root(message, "fraction_printed", 0), "0.5")

    def test_atof_behaviour(self):
        self.assertEqual(sketchmodel.atof("12.5abc"), 12.5)
        self.assertEqual(sketchmodel.atof("abc"), 0.0)
        self.assertEqual(sketchmodel.atof(""), 0.0)


class RoundTripTest(unittest.TestCase):
    def test_all_statuses_arrive(self):
        for status in PrinterStatus:
            printer = sketchmodel.feed(frame_for(status))
            self.assertEqual(printer.status, status.letter)
            self.assertTrue(printer.update_pending)

    def test_temperatures_and_setpoints(self):
        heaters = {
            0: Heater(index=0, current=61.4, active=60.0, state=HeaterState.ACTIVE),
            1: Heater(index=1, current=234.9, active=235.0, state=HeaterState.ACTIVE),
        }
        printer = sketchmodel.feed(frame_for(PrinterStatus.PRINTING, 0.5, heaters))
        self.assertAlmostEqual(printer.heater_act_temp[0], 61.4, places=3)
        self.assertAlmostEqual(printer.heater_act_temp[1], 234.9, places=3)
        self.assertEqual(printer.heater_status[0], 2)
        self.assertEqual(printer.setpoint(0), 60.0)
        self.assertEqual(printer.setpoint(1), 235.0)

    def test_off_heater_has_no_setpoint(self):
        heaters = {
            0: Heater(index=0, current=40.0, state=HeaterState.OFF),
            1: Heater(index=1, current=30.0, state=HeaterState.OFF),
        }
        printer = sketchmodel.feed(frame_for(PrinterStatus.IDLE, heaters=heaters))
        self.assertEqual(printer.heater_status[0], 0)
        self.assertEqual(printer.setpoint(0), 0.0)

    def test_standby_setpoint(self):
        heaters = {
            0: Heater(index=0, current=40.0, standby=45.0, state=HeaterState.STANDBY),
            1: Heater(index=1),
        }
        printer = sketchmodel.feed(frame_for(PrinterStatus.IDLE, heaters=heaters))
        self.assertEqual(printer.heater_status[0], 1)
        self.assertEqual(printer.setpoint(0), 45.0)

    def test_fraction_round_trip(self):
        for progress in (0.0, 0.25, 0.5, 0.75, 0.999):
            printer = sketchmodel.feed(frame_for(PrinterStatus.PRINTING, progress))
            self.assertAlmostEqual(printer.fraction_printed, round(progress, 3), places=3)

    def test_state_is_kept_when_a_frame_is_lost(self):
        heaters = {
            0: Heater(index=0, current=61.4, active=60.0, state=HeaterState.ACTIVE),
            1: Heater(index=1, current=234.9, active=235.0, state=HeaterState.ACTIVE),
        }
        printer = sketchmodel.feed(frame_for(PrinterStatus.PRINTING, 0.4, heaters))
        # abgeschnittener Rahmen: kein '}' -> der Sketch wertet nichts aus
        truncated = frame_for(PrinterStatus.IDLE)[:30]
        printer = sketchmodel.feed(truncated, printer)
        self.assertEqual(printer.status, "P")
        self.assertAlmostEqual(printer.heater_act_temp[0], 61.4, places=3)


class RingRenderTest(unittest.TestCase):
    def test_status_ring_is_filled_uniformly(self):
        printer = sketchmodel.feed(frame_for(PrinterStatus.PAUSED))
        ring = sketchmodel.render_ring(printer, RingConfig(display=99))
        self.assertEqual(set(ring), {STATUS_COLORS["A"]})

    def test_unknown_status_falls_back_to_default(self):
        printer = sketchmodel.SketchPrinter(status="Z")
        ring = sketchmodel.render_ring(printer, RingConfig(display=99))
        self.assertEqual(set(ring), {STATUS_COLORS["default"]})

    def test_progress_bar_grows(self):
        counts = []
        for progress in (0.0, 0.25, 0.5, 1.0):
            printer = sketchmodel.feed(frame_for(PrinterStatus.PRINTING, progress))
            ring = sketchmodel.render_ring(printer, RingConfig(display=99))
            counts.append(sum(1 for pixel in ring if pixel == STATUS_COLORS["printing_done"]))
        self.assertEqual(counts[0], 0)
        self.assertEqual(counts[-1], 16)
        self.assertEqual(counts, sorted(counts))

    def test_full_ring_needs_the_overshoot(self):
        printer = sketchmodel.feed(frame_for(PrinterStatus.PRINTING, 1.0, full_ring=False))
        ring = sketchmodel.render_ring(printer, RingConfig(display=99))
        done = sum(1 for pixel in ring if pixel == STATUS_COLORS["printing_done"])
        self.assertEqual(done, 15, "ohne Ueberschwinger bleibt die letzte LED stehen")

    def test_heating_shows_target_and_actual(self):
        heaters = {
            0: Heater(index=0, current=30.0, active=60.0, state=HeaterState.ACTIVE),
            1: Heater(index=1),
        }
        printer = sketchmodel.feed(frame_for(PrinterStatus.BUSY, heaters=heaters))
        ring = sketchmodel.render_ring(printer, RingConfig(display=0))  # Scale 10
        self.assertEqual(sum(1 for p in ring if p == DEFAULT_HEATER_COLORS["heat_up_done"]), 3)
        self.assertEqual(sum(1 for p in ring if p == DEFAULT_HEATER_COLORS["heat_up"]), 3)
        self.assertEqual(sum(1 for p in ring if p == DEFAULT_HEATER_COLORS["idle"]), 10)

    def test_cooldown_colour_when_heater_is_off(self):
        heaters = {
            0: Heater(index=0, current=50.0, state=HeaterState.OFF),
            1: Heater(index=1),
        }
        printer = sketchmodel.feed(frame_for(PrinterStatus.IDLE, heaters=heaters))
        ring = sketchmodel.render_ring(printer, RingConfig(display=0))
        self.assertEqual(sum(1 for p in ring if p == DEFAULT_HEATER_COLORS["cool_down"]), 5)

    def test_pixel_order_matches_sketch(self):
        """LED 1 landet durch ConvertPosition2PixelIndex am Arrayende."""
        self.assertEqual(sketchmodel.convert_position_to_pixel_index(16, 0, 1, False), 15)
        self.assertEqual(sketchmodel.convert_position_to_pixel_index(16, 0, 16, False), 0)
        self.assertEqual(sketchmodel.convert_position_to_pixel_index(16, 0, 1, True), 0)
        self.assertEqual(sketchmodel.convert_position_to_pixel_index(16, 2, 1, False), 13)


if __name__ == "__main__":
    unittest.main()
