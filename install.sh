#!/bin/bash
# Installation der BLV-NeoPixel-Middleware auf dem Raspberry Pi.
#
#   ./install.sh            normale Installation
#   ./install.sh --no-service   nur venv und Konfiguration, kein systemd
#
set -eu

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Das Paket blv_neopixel ist nicht ins venv installiert und wird nur im
# Projektordner gefunden - auch der Selbsttest unten braucht ihn als Arbeitsordner.
cd "$SRC_DIR"
VENV_DIR="${VENV_DIR:-${HOME}/blv-neopixel-env}"
SERVICE_NAME="blv-neopixel"
WITH_SERVICE=1

for arg in "$@"; do
    case "$arg" in
        --no-service) WITH_SERVICE=0 ;;
        -h|--help)
            sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unbekannte Option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" = "0" ]; then
    echo "Bitte nicht als root starten - das Skript ruft sudo selbst auf." >&2
    exit 1
fi

info() { printf '\n== %s\n' "$1"; }

# ---------------------------------------------------------------- Konfigpfad

CONFIG_DIR=""
for candidate in "${HOME}/printer_data/config" "${HOME}/klipper_config"; do
    if [ -d "$candidate" ]; then CONFIG_DIR="$candidate"; break; fi
done
if [ -z "$CONFIG_DIR" ]; then
    CONFIG_DIR="${HOME}/printer_data/config"
    mkdir -p "$CONFIG_DIR"
fi
CONFIG_FILE="${CONFIG_DIR}/blv-neopixel.conf"

# ---------------------------------------------------------------- Pakete

info "Systempakete pruefen"
MISSING=""
for pkg in python3-venv python3-pip; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then MISSING="$MISSING $pkg"; fi
done
if [ -n "$MISSING" ]; then
    echo "installiere:$MISSING"
    sudo apt-get update
    # shellcheck disable=SC2086
    sudo apt-get install -y $MISSING
else
    echo "alles vorhanden"
fi

info "Virtuelle Umgebung: ${VENV_DIR}"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
"${VENV_DIR}/bin/pip" install -r "${SRC_DIR}/requirements.txt"

# ---------------------------------------------------------------- dialout

if ! id -nG "$USER" | tr ' ' '\n' | grep -qx dialout; then
    info "Benutzer ${USER} der Gruppe dialout hinzufuegen"
    sudo usermod -aG dialout "$USER"
    echo "Hinweis: fuer die eigene Shell erst nach dem naechsten Login wirksam."
fi

# ---------------------------------------------------------------- Konfiguration

SYS_TTY="/sys/class/tty"
DEV_DIR="/dev"

# Herstellername des USB-Geraets hinter einer seriellen Schnittstelle (ohne
# eigenen Namen die Hersteller-ID, so wie udev es fuer by-id macht).
usb_vendor() {
    local dir vendor=""
    dir="$(readlink -f "${SYS_TTY}/$1/device" 2>/dev/null || true)"
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -r "${dir}/idVendor" ]; then
            if [ -r "${dir}/manufacturer" ]; then
                vendor="$(cat "${dir}/manufacturer" 2>/dev/null || true)"
            fi
            if [ -z "$vendor" ]; then vendor="$(cat "${dir}/idVendor")"; fi
            break
        fi
        dir="$(dirname "$dir")"
    done
    printf '%s' "$vendor"
}

# Stabiler Pfad einer Schnittstelle unter /dev/serial/<by-id|by-path>.
stable_path() {
    local link target
    target="$(readlink -f "${DEV_DIR}/$2")"
    for link in "${DEV_DIR}/serial/$1"/*; do
        [ -e "$link" ] || continue
        if is_duplicate_path "$link"; then continue; fi
        if [ "$(readlink -f "$link")" = "$target" ]; then
            printf '%s' "$link"
            return 0
        fi
    done
}

# Neuere udev-Versionen legen je Buchse zwei by-path-Namen an ("...-usb-0:1.2..."
# und "...-usbv2-0:1.2..."); der zweite wird uebergangen.
is_duplicate_path() {
    local plain
    plain="$(printf '%s' "$1" | sed 's/-usbv[0-9]*-/-usb-/')"
    [ "$plain" != "$1" ] && [ -e "$plain" ]
}

# Sucht USB-Seriell-Adapter (CH340, FTDI, CP210x, Prolific). Gezaehlt werden die
# Schnittstellen selbst und nicht die Namen unter /dev/serial/by-id: CH340 haben
# keine Seriennummer, zwei davon teilen sich dort einen einzigen Eintrag.
# Bewusst NICHT mitgezaehlt werden Arduino-, Klipper- und Probe-Geraete (z. B.
# "usb-Arduino__www.arduino.cc__0042_...", "usb-Klipper_...",
# "usb-Cartographer_..."): An einem Drucker sind das in der Regel die MCUs, und
# die Middleware darf dort nichts hinschreiben.
# Ergebnis: ADAPTER_COUNT (Anzahl) und ADAPTER_PATH (nur bei genau einem Treffer).
detect_adapter() {
    local tty name found=""
    ADAPTER_COUNT=0
    ADAPTER_PATH=""
    for tty in "${SYS_TTY}"/ttyUSB* "${SYS_TTY}"/ttyACM*; do
        name="$(basename "$tty")"
        if [ ! -e "$tty" ] || [ ! -e "${DEV_DIR}/${name}" ]; then continue; fi
        case "$(usb_vendor "$name")" in
            1a86*|FTDI*|"Silicon Labs"*|Prolific*)
                ADAPTER_COUNT=$((ADAPTER_COUNT + 1))
                found="$name"
                ;;
        esac
    done
    if [ "$ADAPTER_COUNT" = "1" ]; then
        ADAPTER_PATH="$(stable_path by-id "$found")"
        if [ -z "$ADAPTER_PATH" ]; then ADAPTER_PATH="$(stable_path by-path "$found")"; fi
    fi
}

list_serial_devices() {
    local dev found=0
    for dev in "${DEV_DIR}"/serial/by-id/* "${DEV_DIR}"/serial/by-path/*; do
        [ -e "$dev" ] || continue
        if is_duplicate_path "$dev"; then continue; fi
        found=1
        echo "    $dev -> $(basename "$(readlink -f "$dev")")"
    done
    if [ "$found" = "0" ]; then echo "    (keine)"; fi
}

set_port() {
    python3 - "$CONFIG_FILE" "$1" <<'PY'
import re, sys
path, port = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
text = re.sub(r"^port = .*$", lambda _: ("port = %s" % port).rstrip(), text, count=1, flags=re.M)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
}

info "Konfiguration: ${CONFIG_FILE}"
if [ -f "$CONFIG_FILE" ]; then
    echo "bleibt unveraendert (vorhanden)"
else
    cp "${SRC_DIR}/config/blv-neopixel.conf" "$CONFIG_FILE"
    detect_adapter
    # Ohne eindeutigen Treffer wird der Beispielpfad geleert: Er trifft sonst
    # womoeglich einen fremden CH340.
    set_port "$ADAPTER_PATH"
    if [ -n "$ADAPTER_PATH" ]; then
        echo "serielle Schnittstelle eingetragen: ${ADAPTER_PATH}"
    else
        if [ "$ADAPTER_COUNT" = "0" ]; then
            echo "ACHTUNG: kein USB-Seriell-Adapter (CH340/FTDI/CP210x) gefunden."
        elif [ "$ADAPTER_COUNT" = "1" ]; then
            echo "ACHTUNG: USB-Seriell-Adapter gefunden, aber ohne festen Pfad unter /dev/serial."
        else
            echo "ACHTUNG: ${ADAPTER_COUNT} USB-Seriell-Adapter gefunden - Auswahl nicht eindeutig."
            echo "CH340 haben keine Seriennummer und teilen sich einen by-id-Namen: bei"
            echo "mehreren den Pfad unter /dev/serial/by-path/ verwenden und jeden Adapter"
            echo "an seiner USB-Buchse lassen."
        fi
        echo "Der Port wurde NICHT automatisch gesetzt. Vorhandene Geraete:"
        list_serial_devices
        echo "Arduino-, Klipper- und Probe-Eintraege gehoeren meist zum Drucker -"
        echo "dorthin darf die Middleware nichts senden. Den Pfad des NeoPixel-Arduino"
        echo "bitte in ${CONFIG_FILE} unter [serial] port selbst eintragen."
    fi
fi

# Zeigt der konfigurierte Port auf ein vorhandenes Geraet?
CONFIGURED_PORT="$(sed -n 's/^port[[:space:]]*=[[:space:]]*//p' "$CONFIG_FILE" | head -n 1 | sed 's/[[:space:]]*#.*$//')"
PORT_OK=0
if [ -n "$CONFIGURED_PORT" ] && [ -e "$CONFIGURED_PORT" ]; then PORT_OK=1; fi

# ---------------------------------------------------------------- Selbsttest

info "Selbsttest (ohne Hardware)"
"${VENV_DIR}/bin/python" -m blv_neopixel --config "$CONFIG_FILE" selftest | tail -n 3

# ---------------------------------------------------------------- systemd

if [ "$WITH_SERVICE" = "1" ]; then
    info "systemd-Dienst ${SERVICE_NAME}"
    TMP_UNIT="$(mktemp)"
    sed -e "s|__USER__|${USER}|g" \
        -e "s|__DIR__|${SRC_DIR}|g" \
        -e "s|__PYTHON__|${VENV_DIR}/bin/python|g" \
        -e "s|__CONFIG__|${CONFIG_FILE}|g" \
        "${SRC_DIR}/scripts/${SERVICE_NAME}.service" > "$TMP_UNIT"
    sudo install -m 644 "$TMP_UNIT" "/etc/systemd/system/${SERVICE_NAME}.service"
    rm -f "$TMP_UNIT"
    sudo systemctl daemon-reload
    sudo systemctl enable "${SERVICE_NAME}.service"
    if [ "$PORT_OK" = "1" ]; then
        sudo systemctl restart "${SERVICE_NAME}.service"
        sleep 2
        sudo systemctl --no-pager --lines=15 status "${SERVICE_NAME}.service" || true
    else
        echo
        echo "Der Dienst wurde installiert, aber NICHT gestartet: [serial] port in"
        echo "${CONFIG_FILE} zeigt auf kein vorhandenes Geraet (aktuell: '${CONFIGURED_PORT}')."
        echo "Nach dem Eintragen des richtigen Pfads starten mit:"
        echo "  sudo systemctl restart ${SERVICE_NAME}"
    fi
fi

# ---------------------------------------------------------------- Abschluss

# origin-URL dieses Klons fuer den update_manager-Block uebernehmen
ORIGIN_URL=""
if command -v git >/dev/null 2>&1; then
    ORIGIN_URL="$(git -C "$SRC_DIR" remote get-url origin 2>/dev/null || true)"
fi
if [ -z "$ORIGIN_URL" ]; then
    ORIGIN_URL="<git-url-dieses-repos>"
    ORIGIN_NOTE="  (kein origin-Remote gefunden - URL bitte selbst eintragen)"
elif [ "${ORIGIN_URL#https://}" != "$ORIGIN_URL" ]; then
    ORIGIN_NOTE="  (bei einem privaten Repo braucht der Pi Zugangsdaten:
   Deploy-Key mit SSH-URL oder Token in der URL)"
else
    ORIGIN_NOTE=""
fi

cat <<EOF

== Fertig

Konfiguration : ${CONFIG_FILE}
Python        : ${VENV_DIR}/bin/python
Logs          : journalctl -u ${SERVICE_NAME} -f

WICHTIG: In ${CONFIG_FILE} unter [printer] muss die Sketch-Version zum
geflashten Sketch passen -
  sketch_version = 4.1   fuer Release V4.0/V4.1 (Standard)
  sketch_version = 3.0   fuer Release V3.0

Nuetzliche Befehle - im Projektordner ausfuehren (das Paket ist nicht ins
venv installiert und wird nur dort gefunden):
  cd ${SRC_DIR}
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} config
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} ports
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} selftest --all

Ringe ausprobieren (Dienst vorher anhalten, sonst belegt er die Schnittstelle):
  sudo systemctl stop ${SERVICE_NAME}
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} simulate --preview
  sudo systemctl start ${SERVICE_NAME}

Damit Mainsail/Fluidd den Dienst neu starten darf, in moonraker.conf ergaenzen:

  [update_manager blv-neopixel]
  type: git_repo
  path: ${SRC_DIR}
  origin: ${ORIGIN_URL}
  primary_branch: main
  virtualenv: ${VENV_DIR}
  requirements: requirements.txt
  managed_services: ${SERVICE_NAME}
${ORIGIN_NOTE}

primary_branch ist noetig: Moonraker nimmt sonst "master" an, dieses Repo
nutzt "main". Ausserdem den Dienstnamen fuer Moonraker freigeben und
Moonraker neu starten:

  echo ${SERVICE_NAME} >> $(dirname "$CONFIG_DIR")/moonraker.asvc
  sudo systemctl restart moonraker
EOF
