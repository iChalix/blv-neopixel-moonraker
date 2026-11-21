#!/bin/bash
# Installation der BLV-NeoPixel-Middleware auf dem Raspberry Pi.
#
#   ./install.sh            normale Installation
#   ./install.sh --no-service   nur venv und Konfiguration, kein systemd
#
set -eu

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

# Sucht USB-Seriell-Adapter (CH340, FTDI, CP210x, Prolific) unter
# /dev/serial/by-id. Bewusst NICHT mitgezaehlt werden Arduino-, Klipper- und
# Probe-Geraete (z. B. "usb-Arduino__www.arduino.cc__0042_...", "usb-Klipper_...",
# "usb-Cartographer_..."): An einem Drucker sind das in der Regel die MCUs, und
# die Middleware darf dort nichts hinschreiben.
# Ergebnis: ADAPTER_COUNT (Anzahl) und ADAPTER_PATH (nur bei genau einem Treffer).
detect_adapter() {
    local dev name
    ADAPTER_COUNT=0
    ADAPTER_PATH=""
    for dev in /dev/serial/by-id/*; do
        [ -e "$dev" ] || continue
        name="$(basename "$dev")"
        case "$name" in
            usb-1a86_*|usb-FTDI_*|usb-Silicon_Labs_*|usb-Prolific_*)
                ADAPTER_COUNT=$((ADAPTER_COUNT + 1))
                ADAPTER_PATH="$dev"
                ;;
        esac
    done
    if [ "$ADAPTER_COUNT" != "1" ]; then ADAPTER_PATH=""; fi
}

list_serial_devices() {
    local dev found=0
    for dev in /dev/serial/by-id/*; do
        [ -e "$dev" ] || continue
        found=1
        echo "    $dev"
    done
    if [ "$found" = "0" ]; then echo "    (keine)"; fi
}

info "Konfiguration: ${CONFIG_FILE}"
if [ -f "$CONFIG_FILE" ]; then
    echo "bleibt unveraendert (vorhanden)"
else
    cp "${SRC_DIR}/config/blv-neopixel.conf" "$CONFIG_FILE"
    detect_adapter
    if [ -n "$ADAPTER_PATH" ]; then
        python3 - "$CONFIG_FILE" "$ADAPTER_PATH" <<'PY'
import re, sys
path, port = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
text = re.sub(r"^port = .*$", "port = %s" % port, text, count=1, flags=re.M)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
        echo "serielle Schnittstelle eingetragen: ${ADAPTER_PATH}"
    else
        if [ "$ADAPTER_COUNT" = "0" ]; then
            echo "ACHTUNG: kein USB-Seriell-Adapter (CH340/FTDI/CP210x) gefunden."
        else
            echo "ACHTUNG: ${ADAPTER_COUNT} USB-Seriell-Adapter gefunden - Auswahl nicht eindeutig."
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

Nuetzliche Befehle:
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} config
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} ports
  ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} selftest --all
  sudo systemctl stop ${SERVICE_NAME} && \\
    ${VENV_DIR}/bin/python -m blv_neopixel --config ${CONFIG_FILE} simulate --preview

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
