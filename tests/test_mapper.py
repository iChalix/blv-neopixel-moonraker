"""Abbildung Klipper/Moonraker -> Sketch-Zustand."""

import time
import unittest

from blv_neopixel.config import Config, HeaterMap, default_heaters
from blv_neopixel.mapper import KlipperMapper
from blv_neopixel.state import HeaterState, PrinterStatus


def ready_mapper(**printer_overrides):
    config = Config(heaters=default_heaters())
    for key, value in printer_overrides.items():
        setattr(config.printer, key, value)
    mapper = KlipperMapper(config)
    mapper.set_online(True)
    mapper.set_klippy_state("ready")
    return mapper


class StatusTest(unittest.TestCase):
    def test_before_first_connection_shows_startup(self):
        mapper = KlipperMapper(Config(heaters=default_heaters()))
        state = mapper.build_state()
        self.assertEqual(state.status, PrinterStatus.CONFIGURING)
        self.assertIn("warte", state.note)

    def test_offline_after_connection_loss(self):
        mapper = ready_mapper()
        mapper.set_online(False)
        state = mapper.build_state()
        self.assertEqual(state.status, PrinterStatus.STOPPED)
        self.assertIn("offline", state.note)

    def test_startup(self):
        mapper = KlipperMapper(Config(heaters=default_heaters()))
        mapper.set_online(True)
        mapper.set_klippy_state("startup")
        self.assertEqual(mapper.build_state().status, PrinterStatus.CONFIGURING)

    def test_shutdown(self):
        mapper = ready_mapper()
        mapper.set_klippy_state("shutdown", "MCU 'mcu' shutdown")
        state = mapper.build_state()
        self.assertEqual(state.status, PrinterStatus.STOPPED)
        self.assertIn("shutdown", state.note)

    def test_print_states(self):
        cases = {
            "standby": PrinterStatus.IDLE,
            "printing": PrinterStatus.PRINTING,
            "paused": PrinterStatus.PAUSED,
            "error": PrinterStatus.STOPPED,
        }
        for klipper_state, expected in cases.items():
            mapper = ready_mapper(busy_from_idle_timeout=False)
            mapper.update_objects({"print_stats": {"state": klipper_state}})
            self.assertEqual(mapper.build_state().status, expected, klipper_state)

    def test_pausing_and_resuming(self):
        mapper = ready_mapper()
        mapper.update_objects(
            {"print_stats": {"state": "printing"}, "pause_resume": {"is_paused": True}}
        )
        self.assertEqual(mapper.build_state().status, PrinterStatus.PAUSING)

        mapper = ready_mapper()
        mapper.update_objects(
            {"print_stats": {"state": "paused"}, "pause_resume": {"is_paused": False}}
        )
        self.assertEqual(mapper.build_state().status, PrinterStatus.RESUMING)

    def test_busy_from_idle_timeout(self):
        mapper = ready_mapper()
        mapper.update_objects(
            {"print_stats": {"state": "standby"}, "idle_timeout": {"state": "Printing"}}
        )
        self.assertEqual(mapper.build_state().status, PrinterStatus.BUSY)

    def test_busy_can_be_disabled(self):
        mapper = ready_mapper(busy_from_idle_timeout=False)
        mapper.update_objects(
            {"print_stats": {"state": "standby"}, "idle_timeout": {"state": "Printing"}}
        )
        self.assertEqual(mapper.build_state().status, PrinterStatus.IDLE)

    def test_complete_shows_full_ring_then_idle(self):
        mapper = ready_mapper(complete_hold=30.0)
        mapper.update_objects({"print_stats": {"state": "complete"}})
        state = mapper.build_state()
        self.assertEqual(state.status, PrinterStatus.PRINTING)
        self.assertEqual(state.progress, 1.0)

        # Haltezeit abgelaufen
        mapper._print_state_since = time.monotonic() - 31.0
        self.assertEqual(mapper.build_state().status, PrinterStatus.IDLE)

    def test_cancelled_shows_stopped_then_idle(self):
        mapper = ready_mapper(cancel_hold=10.0)
        mapper.update_objects({"print_stats": {"state": "cancelled"}})
        self.assertEqual(mapper.build_state().status, PrinterStatus.STOPPED)
        mapper._print_state_since = time.monotonic() - 11.0
        self.assertEqual(mapper.build_state().status, PrinterStatus.IDLE)

    def test_holds_can_be_switched_off(self):
        mapper = ready_mapper(complete_hold=0.0, cancel_hold=0.0)
        mapper.update_objects({"print_stats": {"state": "complete"}})
        self.assertEqual(mapper.build_state().status, PrinterStatus.IDLE)


class HeaterTest(unittest.TestCase):
    def test_target_makes_heater_active(self):
        mapper = ready_mapper()
        mapper.update_objects(
            {
                "heater_bed": {"temperature": 59.8, "target": 60.0},
                "extruder": {"temperature": 24.0, "target": 0.0},
            }
        )
        state = mapper.build_state()
        self.assertEqual(state.heater(0).state, HeaterState.ACTIVE)
        self.assertEqual(state.heater(0).active, 60.0)
        self.assertAlmostEqual(state.heater(0).current, 59.8)
        self.assertEqual(state.heater(1).state, HeaterState.OFF)
        self.assertEqual(state.heater(1).setpoint, 0.0)

    def test_sensor_without_target_stays_off(self):
        config = Config(heaters=[HeaterMap(0, "temperature_sensor chamber", "Kammer"), HeaterMap(1, "extruder")])
        mapper = KlipperMapper(config)
        mapper.set_online(True)
        mapper.set_klippy_state("ready")
        mapper.update_objects({"temperature_sensor chamber": {"temperature": 31.5}})
        state = mapper.build_state()
        self.assertEqual(state.heater(0).state, HeaterState.OFF)
        self.assertAlmostEqual(state.heater(0).current, 31.5)

    def test_partial_updates_are_merged(self):
        mapper = ready_mapper()
        mapper.update_objects({"heater_bed": {"temperature": 20.0, "target": 60.0}})
        mapper.update_objects({"heater_bed": {"temperature": 45.0}})
        state = mapper.build_state()
        self.assertAlmostEqual(state.heater(0).current, 45.0)
        self.assertEqual(state.heater(0).active, 60.0)

    def test_custom_mapping(self):
        config = Config(
            heaters=[HeaterMap(0, "heater_bed"), HeaterMap(1, "extruder"), HeaterMap(2, "heater_generic chamber")]
        )
        mapper = KlipperMapper(config)
        mapper.set_online(True)
        mapper.set_klippy_state("ready")
        mapper.update_objects({"heater_generic chamber": {"temperature": 48.0, "target": 50.0}})
        state = mapper.build_state()
        self.assertEqual(state.heater(2).state, HeaterState.ACTIVE)
        self.assertIn("heater_generic chamber", mapper.subscription())


class ProgressTest(unittest.TestCase):
    def test_file_progress(self):
        mapper = ready_mapper(progress_source="file")
        mapper.update_objects(
            {"print_stats": {"state": "printing"}, "display_status": {"progress": 0.42}}
        )
        self.assertAlmostEqual(mapper.build_state().progress, 0.42)

    def test_sdcard_progress(self):
        mapper = ready_mapper(progress_source="sdcard")
        mapper.update_objects(
            {
                "print_stats": {"state": "printing"},
                "display_status": {"progress": 0.9},
                "virtual_sdcard": {"progress": 0.31},
            }
        )
        self.assertAlmostEqual(mapper.build_state().progress, 0.31)

    def test_layer_progress(self):
        mapper = ready_mapper(progress_source="layer")
        mapper.update_objects(
            {
                "print_stats": {"state": "printing", "info": {"current_layer": 50, "total_layer": 200}},
            }
        )
        self.assertAlmostEqual(mapper.build_state().progress, 0.25)

    def test_layer_progress_falls_back(self):
        mapper = ready_mapper(progress_source="layer")
        mapper.update_objects(
            {
                "print_stats": {"state": "printing", "info": {"current_layer": None, "total_layer": None}},
                "display_status": {"progress": 0.6},
            }
        )
        self.assertAlmostEqual(mapper.build_state().progress, 0.6)

    def test_progress_is_clamped(self):
        mapper = ready_mapper()
        mapper.update_objects(
            {"print_stats": {"state": "printing"}, "display_status": {"progress": 3.0}}
        )
        self.assertEqual(mapper.build_state().progress, 1.0)

    def test_garbage_values_do_not_crash(self):
        mapper = ready_mapper()
        mapper.update_objects(
            {
                "print_stats": {"state": "printing"},
                "display_status": {"progress": None},
                "heater_bed": {"temperature": "kaputt", "target": None},
            }
        )
        state = mapper.build_state()
        self.assertEqual(state.progress, 0.0)
        self.assertEqual(state.heater(0).current, 0.0)


class SubscriptionTest(unittest.TestCase):
    def test_contains_base_objects_and_heaters(self):
        mapper = KlipperMapper(Config(heaters=default_heaters()))
        objects = mapper.subscription()
        for name in ("print_stats", "display_status", "idle_timeout", "pause_resume", "virtual_sdcard"):
            self.assertIn(name, objects)
        self.assertIn("heater_bed", objects)
        self.assertIn("extruder", objects)


if __name__ == "__main__":
    unittest.main()
