"""Rahmenerzeugung und ihre Grenzen."""

import unittest

from blv_neopixel import duet
from blv_neopixel.state import Heater, HeaterState, PrinterState, PrinterStatus


def state(status=PrinterStatus.IDLE, progress=0.0, heaters=None):
    return PrinterState(status=status, heaters=heaters or {}, progress=progress)


def two_heaters():
    return {
        0: Heater(index=0, current=61.4, active=60.0, state=HeaterState.ACTIVE),
        1: Heater(index=1, current=24.2, active=0.0, state=HeaterState.OFF),
    }


class FrameFormatTest(unittest.TestCase):
    def test_exact_frame(self):
        frame = duet.build_frame(state(PrinterStatus.PRINTING, 0.5, two_heaters()))
        self.assertEqual(
            frame,
            '{"status":"P","heaters":[61.4,24.2],"active":[60.0,0.0],'
            '"standby":[0.0,0.0],"hstat":[2,0],"fraction_printed":0.5}\n',
        )

    def test_key_order_is_stable(self):
        frame = duet.build_frame(state(heaters=two_heaters()))
        positions = [frame.find('"%s"' % key) for key in duet.FRAME_KEYS]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(-1, positions)

    def test_no_whitespace_after_separators(self):
        frame = duet.build_frame(state(heaters=two_heaters()))
        self.assertNotIn(": ", frame)
        self.assertNotIn(", ", frame)

    def test_single_object_only(self):
        frame = duet.build_frame(state(heaters=two_heaters()))
        self.assertEqual(frame.count("{"), 1)
        self.assertEqual(frame.count("}"), 1)
        self.assertTrue(frame.endswith("}\n"))

    def test_status_is_single_letter(self):
        for status in PrinterStatus:
            frame = duet.build_frame(state(status, heaters=two_heaters()))
            self.assertIn('"status":"%s"' % status.letter, frame)


class ArrayRulesTest(unittest.TestCase):
    def test_single_heater_is_padded(self):
        """Der V3.0-Parser braucht mindestens zwei Array-Werte."""
        frame = duet.build_frame(state(heaters={0: Heater(index=0, current=42.0)}))
        self.assertIn('"heaters":[42.0,0.0]', frame)

    def test_empty_heaters_are_padded(self):
        frame = duet.build_frame(state())
        self.assertIn('"heaters":[0.0,0.0]', frame)

    def test_gaps_are_filled(self):
        heaters = {
            0: Heater(index=0, current=55.0, active=55.0, state=HeaterState.ACTIVE),
            2: Heater(index=2, current=30.0),
        }
        frame = duet.build_frame(state(heaters=heaters))
        self.assertIn('"heaters":[55.0,0.0,30.0]', frame)
        self.assertIn('"hstat":[2,0,0]', frame)

    def test_five_heaters_fit_the_buffers(self):
        heaters = {
            i: Heater(index=i, current=199.9, active=250.0, state=HeaterState.ACTIVE)
            for i in range(5)
        }
        frame = duet.build_frame(state(heaters=heaters))
        self.assertLessEqual(len(frame) - 1, duet.MAX_FRAME_BYTES)
        stripped = duet.strip_quotes(frame)
        for key in ("heaters", "active", "standby", "hstat"):
            start = stripped.find(key) + len(key) + 2
            inner = stripped[start : stripped.find("]", start)]
            self.assertLess(len(inner), duet.MAX_ARRAY_CHARS)


class FractionTest(unittest.TestCase):
    def test_plain_values(self):
        self.assertEqual(duet.encode_fraction(0.0), 0.0)
        self.assertEqual(duet.encode_fraction(0.5), 0.5)
        self.assertEqual(duet.encode_fraction(0.1234), 0.123)

    def test_clamping(self):
        self.assertEqual(duet.encode_fraction(-5.0), 0.0)
        self.assertEqual(duet.encode_fraction(float("nan")), 0.0)

    def test_full_ring_overshoot(self):
        self.assertEqual(duet.encode_fraction(1.0), duet.FULL_RING_FRACTION)
        self.assertGreater(duet.FULL_RING_FRACTION, 1.0)

    def test_full_ring_can_be_disabled(self):
        self.assertEqual(duet.encode_fraction(1.0, full_ring=False), 1.0)

    def test_progress_only_when_printing(self):
        frame = duet.build_frame(state(PrinterStatus.PAUSED, 0.75, two_heaters()))
        self.assertIn('"fraction_printed":0', frame)


class ValidationTest(unittest.TestCase):
    def test_rejects_nested_object(self):
        with self.assertRaises(duet.FrameError):
            duet.validate_frame(
                '{"status":"I","heaters":[1.0,2.0],"active":[0.0,0.0],'
                '"standby":[0.0,0.0],"hstat":[0,0],"fraction_printed":0,"x":{"y":1}}\n'
            )

    def test_rejects_space_after_colon(self):
        with self.assertRaises(duet.FrameError):
            duet.validate_frame(
                '{"status": "I","heaters":[1.0,2.0],"active":[0.0,0.0],'
                '"standby":[0.0,0.0],"hstat":[0,0],"fraction_printed":0}\n'
            )

    def test_rejects_single_value_array(self):
        with self.assertRaises(duet.FrameError):
            duet.validate_frame(
                '{"status":"I","heaters":[1.0],"active":[0.0],'
                '"standby":[0.0],"hstat":[0],"fraction_printed":0}\n'
            )

    def test_rejects_missing_newline(self):
        with self.assertRaises(duet.FrameError):
            duet.validate_frame('{"status":"I"}')

    def test_rejects_oversized_frame(self):
        heaters = {i: Heater(index=i) for i in range(5)}
        frame = duet.build_frame(state(heaters=heaters))
        padded = frame[:-2] + "," + "x" * duet.MAX_FRAME_BYTES + "}\n"
        with self.assertRaises(duet.FrameError):
            duet.validate_frame(padded)


class StripQuotesTest(unittest.TestCase):
    def test_matches_sketch_behaviour(self):
        frame = duet.build_frame(state(PrinterStatus.IDLE, heaters=two_heaters()))
        self.assertEqual(
            duet.strip_quotes(frame),
            "{status:I,heaters:[61.4,24.2],active:[60.0,0.0],"
            "standby:[0.0,0.0],hstat:[2,0],fraction_printed:0.0}",
        )


if __name__ == "__main__":
    unittest.main()
