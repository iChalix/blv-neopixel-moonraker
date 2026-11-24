"""Adaptererkennung aus install.sh gegen nachgebaute /sys- und /dev-Baeume."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "blv-neopixel.conf")

CH340 = ("1a86", "")
CH340_ID = "usb-1a86_USB2.0-Ser_-if00-port0"


def config_section():
    """Funktionen und Ablauf des Abschnitts, der die Konfiguration anlegt."""
    with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as handle:
        text = handle.read()
    start = text.index('SYS_TTY="/sys/class/tty"')
    end = text.index("# ---------------------------------------------------------------- Selbsttest")
    # die beiden Wurzelverzeichnisse kommen aus dem Test
    return re.sub(r"^(SYS_TTY|DEV_DIR)=.*$", "", text[start:end], flags=re.M)


@unittest.skipUnless(shutil.which("bash"), "bash fehlt")
class DetectAdapterTest(unittest.TestCase):
    def setUp(self):
        # realpath: unter macOS ist das Temp-Verzeichnis selbst ein Symlink
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.config = os.path.join(self.root, "blv-neopixel.conf")
        for name in ("sys/class/tty", "dev/serial/by-id", "dev/serial/by-path"):
            os.makedirs(os.path.join(self.root, name))

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def add(self, tty, socket, vendor, by_id=None, by_path=("usb", "usbv2")):
        """USB-Geraet an einer Buchse anlegen, so wie Kernel und udev es tun."""
        vendor_id, manufacturer = vendor
        usb = self.path("sys/devices/usb1/1-1", "1-%s" % socket)
        interface = os.path.join(usb, "1-%s:1.0" % socket)
        port = os.path.join(interface, tty) if tty.startswith("ttyUSB") else interface
        node = os.path.join(port, "tty", tty)
        os.makedirs(node)
        # der Hub darueber hat eine eigene Hersteller-ID
        self.write(self.path("sys/devices/usb1/1-1/idVendor"), "1a86")
        self.write(os.path.join(usb, "idVendor"), vendor_id)
        if manufacturer:
            self.write(os.path.join(usb, "manufacturer"), manufacturer)
        os.symlink(port, os.path.join(node, "device"))
        os.symlink(node, self.path("sys/class/tty", tty))
        self.write(self.path("dev", tty), "")
        for kind in by_path:
            name = "platform-3f980000.usb-%s-0:%s:1.0-port0" % (kind, socket)
            os.symlink("../../" + tty, self.path("dev/serial/by-path", name))
        if by_id:
            link = self.path("dev/serial/by-id", by_id)
            if os.path.islink(link):  # gleicher Name: der letzte gewinnt
                os.unlink(link)
            os.symlink("../../" + tty, link)

    @staticmethod
    def write(path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def run_section(self):
        """Abschnitt ausfuehren; liefert (Anzahl, eingetragener Port, Ausgabe)."""
        script = (
            'set -eu\ninfo() { :; }\nSYS_TTY="$1/sys/class/tty"\nDEV_DIR="$1/dev"\n'
            'CONFIG_FILE="$2"\nSRC_DIR="$3"\n'
            + config_section()
            + '\nif [ -n "${ADAPTER_COUNT+x}" ]; then echo "COUNT=${ADAPTER_COUNT}"; fi\n'
            + 'echo "PORT_OK=${PORT_OK}"\n'
        )
        done = subprocess.run(
            ["bash", "-c", script, "bash", self.root, self.config, ROOT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        count = re.search(r"^COUNT=(\d+)$", done.stdout, re.M)
        with open(self.config, encoding="utf-8") as handle:
            port = re.search(r"^port =(.*)$", handle.read(), re.M).group(1).strip()
        return int(count.group(1)) if count else None, port, done.stdout

    def test_no_adapter(self):
        count, port, output = self.run_section()
        self.assertEqual((count, port), (0, ""))
        self.assertIn("(keine)", output)
        self.assertIn("PORT_OK=0", output)

    def test_single_ch340(self):
        self.add("ttyUSB0", "1.2", CH340, CH340_ID)
        count, port, output = self.run_section()
        self.assertEqual((count, port), (1, self.path("dev/serial/by-id", CH340_ID)))
        self.assertIn("PORT_OK=1", output)

    def test_two_ch340_share_one_name(self):
        self.add("ttyUSB0", "1.2", CH340, CH340_ID)
        self.add("ttyUSB1", "1.3", CH340, CH340_ID)
        count, port, output = self.run_section()
        self.assertEqual((count, port), (2, ""))
        self.assertIn("PORT_OK=0", output)
        # jede Buchse genau einmal, mit dem usb-Namen
        for socket, tty in (("1.2", "ttyUSB0"), ("1.3", "ttyUSB1")):
            self.assertIn("usb-usb-0:%s:1.0-port0 -> %s\n" % (socket, tty), output)
        self.assertNotIn("usbv2", output)

    def test_example_port_does_not_stay_in_place(self):
        # der Beispielpfad aus der Vorlage darf nicht auf irgendeinen CH340 zeigen
        with open(CONFIG, encoding="utf-8") as handle:
            self.assertIn("by-id/" + CH340_ID, handle.read())
        self.add("ttyUSB0", "1.2", CH340, CH340_ID)
        self.add("ttyUSB1", "1.3", ("0403", "FTDI"), "usb-FTDI_FT232R_USB_UART_A1-if00-port0")
        self.assertEqual(self.run_section()[:2], (2, ""))

    def test_without_by_id_uses_by_path(self):
        self.add("ttyUSB0", "1.2", CH340)
        _, port, _ = self.run_section()
        self.assertEqual(
            port, self.path("dev/serial/by-path/platform-3f980000.usb-usb-0:1.2:1.0-port0")
        )

    def test_usbv2_is_used_when_it_is_the_only_name(self):
        self.add("ttyUSB0", "1.2", CH340, by_path=("usbv2",))
        self.assertIn("usb-usbv2-0:1.2", self.run_section()[1])

    def test_adapter_without_stable_path(self):
        self.add("ttyUSB0", "1.2", CH340, by_path=())
        count, port, output = self.run_section()
        self.assertEqual((count, port), (1, ""))
        self.assertIn("ohne festen Pfad", output)

    def test_printer_boards_are_not_counted(self):
        self.add("ttyACM0", "1.2", ("2341", "Arduino (www.arduino.cc)"), "usb-Arduino__www.arduino.cc__0042-if00")
        self.add("ttyACM1", "1.3", ("1d50", "Klipper"), "usb-Klipper_rp2040_1-if00")
        self.add("ttyUSB0", "1.4", ("10c4", "ITead"), "usb-ITead_Sonoff_Zigbee-if00-port0")
        count, port, _ = self.run_section()
        self.assertEqual((count, port), (0, ""))

        os.unlink(self.config)
        self.add("ttyUSB1", "1.5", ("10c4", "Silicon Labs"), "usb-Silicon_Labs_CP2102-if00-port0")
        count, port, _ = self.run_section()
        self.assertEqual(count, 1)
        self.assertTrue(port.endswith("usb-Silicon_Labs_CP2102-if00-port0"))

    def test_existing_config_is_kept(self):
        self.add("ttyUSB0", "1.2", CH340, CH340_ID)
        self.write(self.config, "[serial]\nport = /dev/eigener")
        count, port, _ = self.run_section()
        self.assertEqual((count, port), (None, "/dev/eigener"))


if __name__ == "__main__":
    unittest.main()
