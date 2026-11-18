"""Rahmen fuer Sketch V4.x (Objektmodell) und das nachgebaute Verhalten."""

import json
import unittest

from blv_neopixel import duet_om, sketchmodel
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
        frame = duet_om.build_frame(state(PrinterStatus.PRINTING, 0.5, two_heaters()))
        self.assertEqual(
            frame,
            '{"state":{"status":"processing"},'
            '"heaters":[{"current":61.4,"active":60.0,"standby":0.0,"state":"active"},'
            '{"current":24.2,"active":0.0,"standby":0.0,"state":"off"}],'
            '"timesLeft":{"slicer":500},"job":{"duration":500,"warmUpDuration":0}}\n',
        )

    def test_key_order(self):
        payload = json.loads(duet_om.build_frame(state(heaters=two_heaters()))[:-1])
        self.assertEqual(list(payload), ["state", "heaters", "timesLeft", "job"])

    def test_times_left_before_job(self):
        """job.duration wird nur uebernommen, solange printRemaining > 0 ist."""
        body = duet_om.build_frame(state(PrinterStatus.PRINTING, 0.5))[:-1]
        self.assertLess(body.find('"timesLeft"'), body.find('"job"'))

    def test_only_one_array(self):
        frame = duet_om.build_frame(state(heaters=two_heaters()))
        self.assertEqual(frame.count("["), 1)
        self.assertEqual(frame.count("]"), 1)

    def test_single_heater_is_not_padded(self):
        """Anders als V3.0 kommt der Streaming-Parser mit einem Wert zurecht."""
        payload = json.loads(duet_om.build_frame(state(heaters={0: Heater(index=0)}))[:-1])
        self.assertEqual(len(payload["heaters"]), 1)

    def test_gaps_are_filled(self):
        heaters = {
            0: Heater(index=0, current=55.0, active=55.0, state=HeaterState.ACTIVE),
            2: Heater(index=2, current=30.0),
        }
        payload = json.loads(duet_om.build_frame(state(heaters=heaters))[:-1])
        self.assertEqual(len(payload["heaters"]), 3)
        self.assertEqual(payload["heaters"][1]["state"], "off")
        self.assertEqual(payload["heaters"][2]["current"], 30.0)

    def test_status_words(self):
        for status in PrinterStatus:
            payload = json.loads(duet_om.build_frame(state(status))[:-1])
            word = payload["state"]["status"]
            self.assertIn(word, duet_om.KNOWN_STATUS_WORDS)
            self.assertEqual(sketchmodel.V4_STATUS_LETTERS[word], status.letter)

    def test_tokens_fit_the_parser_buffer(self):
        frame = duet_om.build_frame(state(PrinterStatus.PRINTING, 0.5, two_heaters()))
        for token in frame.replace("{", " ").replace("}", " ").replace("[", " ").split(","):
            for part in token.split(":"):
                self.assertLessEqual(len(part.strip().strip('"]')), duet_om.MAX_TOKEN_CHARS)


class ProgressTest(unittest.TestCase):
    def test_encoding_matches_the_sketch_formula(self):
        for progress in (0.0, 0.1, 0.25, 0.5, 0.75, 0.999):
            duration, warm_up, remaining = duet_om.encode_progress(progress)
            self.assertAlmostEqual(
                duet_om.decoded_fraction(duration, warm_up, remaining), progress, places=3
            )

    def test_remaining_is_never_zero(self):
        for progress in (0.0, 0.5, 1.0):
            _, _, remaining = duet_om.encode_progress(progress)
            self.assertGreaterEqual(remaining, 1)

    def test_values_stay_in_uint16(self):
        for progress in (0.0, 0.5, 1.0):
            for value in duet_om.encode_progress(progress):
                self.assertLessEqual(value, duet_om.UINT16_MAX)

    def test_clamping(self):
        self.assertEqual(duet_om.encode_progress(-1.0)[0], 0)
        self.assertEqual(duet_om.encode_progress(float("nan"))[0], 0)
        self.assertEqual(duet_om.encode_progress(5.0)[0], duet_om.PROGRESS_TICKS - 1)

    def test_progress_only_when_printing(self):
        payload = json.loads(duet_om.build_frame(state(PrinterStatus.PAUSED, 0.75))[:-1])
        self.assertEqual(payload["job"]["duration"], 0)


class ValidationTest(unittest.TestCase):
    def test_accepts_own_frames(self):
        for status in PrinterStatus:
            duet_om.validate_frame(duet_om.build_frame(state(status, 0.5, two_heaters())))

    def test_rejects_wrong_key_order(self):
        with self.assertRaises(duet_om.FrameError):
            duet_om.validate_frame(
                '{"state":{"status":"processing"},"heaters":[{"current":1.0,"active":0.0,'
                '"standby":0.0,"state":"off"}],"job":{"duration":500,"warmUpDuration":0},'
                '"timesLeft":{"slicer":500}}\n'
            )

    def test_rejects_nested_array(self):
        with self.assertRaises(duet_om.FrameError):
            duet_om.validate_frame(
                '{"state":{"status":"idle"},"heaters":[{"current":1.0,"active":0.0,'
                '"standby":0.0,"state":"off"}],"timesLeft":{"slicer":1},'
                '"job":{"duration":0,"warmUpDuration":0},"extra":[1,2]}\n'
            )

    def test_rejects_too_many_heaters(self):
        heaters = {i: Heater(index=i) for i in range(5)}
        payload = duet_om.build_payload(state(heaters=heaters))
        payload["heaters"].append(payload["heaters"][0])
        frame = json.dumps(payload, separators=(",", ":")) + "\n"
        with self.assertRaises(duet_om.FrameError):
            duet_om.validate_frame(frame)

    def test_rejects_unknown_status(self):
        with self.assertRaises(duet_om.FrameError):
            duet_om.validate_frame(
                '{"state":{"status":"tanzend"},"heaters":[{"current":1.0,"active":0.0,'
                '"standby":0.0,"state":"off"}],"timesLeft":{"slicer":1},'
                '"job":{"duration":0,"warmUpDuration":0}}\n'
            )

    def test_rejects_missing_newline(self):
        frame = duet_om.build_frame(state())
        with self.assertRaises(duet_om.FrameError):
            duet_om.validate_frame(frame.rstrip("\n"))

    def test_rejects_long_token(self):
        payload = duet_om.build_payload(state())
        payload["state"]["status"] = "x" * 40
        with self.assertRaises(duet_om.FrameError):
            duet_om.validate_frame(json.dumps(payload, separators=(",", ":")) + "\n")


class SketchModelTest(unittest.TestCase):
    def test_statuses_round_trip(self):
        for status in PrinterStatus:
            printer = sketchmodel.feed_v4(duet_om.build_frame(state(status)))
            self.assertEqual(printer.status, status.letter)
            self.assertTrue(printer.update_pending)

    def test_temperatures_land_on_the_right_index(self):
        heaters = {
            0: Heater(index=0, current=61.4, active=60.0, state=HeaterState.ACTIVE),
            1: Heater(index=1, current=234.9, active=235.0, state=HeaterState.ACTIVE),
        }
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.PRINTING, 0.5, heaters)))
        self.assertAlmostEqual(printer.heater_act_temp[0], 61.4, places=3)
        self.assertAlmostEqual(printer.heater_act_temp[1], 234.9, places=3)
        self.assertEqual(printer.heater_status[0], 2)
        self.assertEqual(printer.setpoint(0), 60.0)
        self.assertEqual(printer.setpoint(1), 235.0)

    def test_standby_setpoint(self):
        heaters = {0: Heater(index=0, current=40.0, standby=45.0, state=HeaterState.STANDBY)}
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(heaters=heaters)))
        self.assertEqual(printer.heater_status[0], 1)
        self.assertEqual(printer.setpoint(0), 45.0)

    def test_fraction_round_trip(self):
        for progress in (0.0, 0.25, 0.5, 0.999):
            printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.PRINTING, progress)))
            self.assertAlmostEqual(printer.fraction_printed, progress, places=3)

    def test_fraction_cannot_exceed_one(self):
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.PRINTING, 1.0)))
        self.assertLess(printer.fraction_printed, 1.0)
        ring = sketchmodel.render_ring(printer, sketchmodel.RingConfig(display=99))
        done = sum(1 for p in ring if p == sketchmodel.STATUS_COLORS["printing_done"])
        self.assertEqual(done, 15, "V4.x kann die letzte LED rechnerisch nicht fuellen")

    def test_job_is_ignored_when_times_left_comes_last(self):
        """Beweist, warum die Schluesselreihenfolge festgelegt ist."""
        payload = duet_om.build_payload(state(PrinterStatus.PRINTING, 0.5))
        swapped = {
            "state": payload["state"],
            "heaters": payload["heaters"],
            "job": payload["job"],
            "timesLeft": payload["timesLeft"],
        }
        frame = json.dumps(swapped, separators=(",", ":")) + "\n"
        printer = sketchmodel.feed_v4(frame)
        self.assertEqual(printer.print_duration, 0)
        self.assertEqual(printer.fraction_printed, 0.0)

    def test_idle_resets_the_print_times(self):
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.PRINTING, 0.5)))
        self.assertGreater(printer.print_duration, 0)
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.IDLE)), printer)
        self.assertEqual(printer.fraction_printed, 0.0)

    def test_incomplete_line_is_ignored(self):
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.PAUSED)))
        before = printer.status
        printer = sketchmodel.feed_v4('{"state":{"status":"proc', printer)
        self.assertEqual(printer.status, before)

    def test_heater_graph_uses_the_same_logic_as_v30(self):
        heaters = {0: Heater(index=0, current=30.0, active=60.0, state=HeaterState.ACTIVE)}
        printer = sketchmodel.feed_v4(duet_om.build_frame(state(PrinterStatus.BUSY, heaters=heaters)))
        ring = sketchmodel.render_ring(printer, sketchmodel.RingConfig(display=0))  # Scale 10
        colors = sketchmodel.DEFAULT_HEATER_COLORS
        self.assertEqual(sum(1 for p in ring if p == colors["heat_up_done"]), 3)
        self.assertEqual(sum(1 for p in ring if p == colors["heat_up"]), 3)
        self.assertEqual(sum(1 for p in ring if p == colors["idle"]), 10)


if __name__ == "__main__":
    unittest.main()
