#!/bin/sh
# Installa o aggiorna Voxarium su Ubuntu/Debian. Gira come utente normale, con un servizio systemd dell'utente:
# sudo serve solo la prima volta, per installare i pacchetti mancanti e aprire le porte nel firewall (ufw).
#   sh install.sh            prima installazione (o reinstallazione)
#   sh install.sh --update   aggiornamento lanciato dal pannello: config.json, instances.json e certs/ restano
set -eu

SRC_DIR=$(cd "$(dirname "$0")" && pwd)
APP_DIR="${VOXARIUM_DIR:-$HOME/.local/share/voxarium}"
UNIT_DIR="$HOME/.config/systemd/user"
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1

if [ "$(id -u)" -eq 0 ]; then
    echo "Non eseguire come root: avvia lo script con il tuo utente (sudo viene chiesto solo se serve)."
    exit 1
fi

# 1. Pacchetti di sistema (solo alla prima installazione)
if [ $UPDATE -eq 0 ]; then
    PKGS=""
    command -v python3 >/dev/null 2>&1 || PKGS="$PKGS python3"
    python3 -c "import ensurepip" >/dev/null 2>&1 || PKGS="$PKGS python3-venv"
    if ! command -v java >/dev/null 2>&1; then
        # Minecraft recente vuole Java 25; se la distribuzione non lo ha ancora, Java 21
        if apt-cache show openjdk-25-jre-headless >/dev/null 2>&1; then
            PKGS="$PKGS openjdk-25-jre-headless"
        else
            PKGS="$PKGS openjdk-21-jre-headless"
        fi
    fi
    if [ -n "$PKGS" ]; then
        echo "Installo i pacchetti mancanti:$PKGS (viene chiesta la password di amministratore)"
        sudo apt-get update
        # shellcheck disable=SC2086
        sudo apt-get install -y $PKGS
    fi
fi

# 2. File del programma: i dati personali e i certificati nella cartella di installazione non si toccano
mkdir -p "$APP_DIR"
for item in "$SRC_DIR"/*; do
    name=$(basename "$item")
    case "$name" in
        install.sh|voxarium.service|config.json|instances.json|launcher_config.json|certs) continue ;;
    esac
    rm -rf "${APP_DIR:?}/$name"
    cp -R "$item" "$APP_DIR/$name"
done
cp "$SRC_DIR/install.sh" "$APP_DIR/install.sh"

# 3. Python: ambiente separato con le librerie del programma, poi solo i moduli compilati (come su Windows)
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
"$APP_DIR/venv/bin/python" -m compileall -q -b -o 2 "$APP_DIR"/*.py "$APP_DIR/routes" >/dev/null
find "$APP_DIR" -maxdepth 2 -name "*.py" -not -path "$APP_DIR/venv/*" -delete
find "$APP_DIR" -maxdepth 2 -name "__pycache__" -not -path "$APP_DIR/venv/*" -exec rm -rf {} +

# 4. Servizio systemd dell'utente: parte all'accensione anche senza accesso (linger)
mkdir -p "$UNIT_DIR"
sed "s#@APP_DIR@#$APP_DIR#g" "$SRC_DIR/voxarium.service" > "$UNIT_DIR/voxarium.service"
systemctl --user daemon-reload
if [ $UPDATE -eq 0 ]; then
    systemctl --user enable voxarium.service
    loginctl enable-linger "$(id -un)" 2>/dev/null || sudo loginctl enable-linger "$(id -un)" || true
    # Porte: pannello in casa (8080), accesso da internet (8443), Minecraft (25565). Solo se ufw è attivo.
    if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
        sudo ufw allow 8080/tcp comment "Voxarium pannello"
        sudo ufw allow 8443/tcp comment "Voxarium da internet"
        sudo ufw allow 25565 comment "Minecraft"
    fi
fi
systemctl --user restart voxarium.service

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT=$("$APP_DIR/venv/bin/python" -c "import json; print(json.load(open('$APP_DIR/config.json')).get('web_port', 8080))" 2>/dev/null || echo 8080)
echo "Voxarium è in esecuzione. Apri il pannello nel browser: http://${IP:-localhost}:${PORT}"
