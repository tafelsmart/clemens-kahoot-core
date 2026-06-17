#!/usr/bin/env bash
# --------------------------------------------------------------------------
#  Bot Deployment Assistant
#  Interactive setup for a Python/Telegram bot running under systemd or screen
# --------------------------------------------------------------------------
set -euo pipefail
IFS=$'\n\t'

# ---------- Configuration -------------------------------------------------
REPO_URL="https://github.com/tafelsmart/clemens-kahoot-core.git"
PROJECT_DIR="${HOME}/clemens-kahoot-core"
SERVICE_NAME="kahoot-bot"
LOG_FILE="${HOME}/bot-setup.log"

# ---------- Helpers ----------------------------------------------------
C_RESET="\033[0m"; C_BLUE="\033[1;34m"; C_GREEN="\033[1;32m"; C_YELLOW="\033[1;33m"; C_RED="\033[1;31m"

log()    { echo -e "${C_BLUE}[INFO]${C_RESET} $*"  | tee -a "$LOG_FILE"; }
ok()     { echo -e "${C_GREEN}[ OK ]${C_RESET} $*" | tee -a "$LOG_FILE"; }
warn()   { echo -e "${C_YELLOW}[WARN]${C_RESET} $*" | tee -a "$LOG_FILE"; }
fail()   { echo -e "${C_RED}[FAIL]${C_RESET} $*"   | tee -a "$LOG_FILE"; exit 1; }

trap 'fail "Setup aborted at line $LINENO. See ${LOG_FILE} for details."' ERR

ask_yes_no() {
    local prompt="$1" default="${2:-n}" reply
    while true; do
        read -rp "$prompt [y/n]: " reply
        reply="${reply:-$default}"
        case "$reply" in
            y|Y) return 0 ;;
            n|N) return 1 ;;
            *) echo "Please answer y or n." ;;
        esac
    done
}

ask_nonempty() {
    local prompt="$1" var
    while true; do
        read -rp "$prompt: " var
        [[ -n "$var" ]] && { echo "$var"; return; }
        echo "This field cannot be empty."
    done
}

ask_secret() {
    local prompt="$1" var
    while true; do
        read -rsp "$prompt: " var
        echo
        [[ -n "$var" ]] && { echo "$var"; return; }
        echo "This field cannot be empty."
    done
}

require_cmd() { command -v "$1" >/dev/null 2>&1; }

# ---------- Banner ----------------------------------------------------
clear
echo "=================================================================="
echo "  Bot Deployment Assistant"
echo "  Automated environment setup, dependency installation, and"
echo "  service configuration."
echo "=================================================================="
echo

# ---------- 1. Gather configuration ------------------------------------
CONFIGURE_SYSTEM=false
if ask_yes_no "Configure WiFi and power settings for unattended 24/7 operation now?"; then
    CONFIGURE_SYSTEM=true
    WIFI_SSID=$(ask_nonempty "WiFi SSID")
    WIFI_PASS=$(ask_secret "WiFi password")
fi

TG_TOKEN=$(ask_secret "Telegram bot token")
if [[ ! "$TG_TOKEN" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
    warn "That doesn't look like a typical Telegram bot token format. Continuing anyway."
fi

RUN_MODE="manual"
if ask_yes_no "Run the bot as a systemd service (recommended for 24/7 reliability)?" "y"; then
    RUN_MODE="systemd"
elif ask_yes_no "Run the bot now in a detached 'screen' session instead?"; then
    RUN_MODE="screen"
fi

echo
log "Configuration captured. Beginning setup..."
echo

# ---------- 2. Network & power configuration ----------------------------
if $CONFIGURE_SYSTEM; then
    log "Connecting to WiFi network '${WIFI_SSID}'..."
    if sudo nmcli device wifi connect "$WIFI_SSID" password "$WIFI_PASS" >>"$LOG_FILE" 2>&1; then
        ok "WiFi connected."
    else
        warn "WiFi connection failed. Check the SSID/password and your adapter status."
    fi

    log "Disabling sleep/suspend/hibernate targets..."
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >>"$LOG_FILE" 2>&1
    ok "Sleep modes disabled."

    POWERSAVE_CONF="/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf"
    if [[ -f "$POWERSAVE_CONF" ]]; then
        log "Disabling WiFi power-save mode..."
        sudo sed -i 's/wifi.powersave = 3/wifi.powersave = 2/g' "$POWERSAVE_CONF"
        sudo systemctl restart NetworkManager
        ok "WiFi power-save disabled."
    fi
fi

# ---------- 3. Base packages (idempotent, faster) ------------------------
log "Updating package lists..."
sudo apt-get update -qq

log "Installing required system packages..."
REQUIRED_PKGS=(python3 python3-venv python3-pip git curl screen jq)
MISSING_PKGS=()
for pkg in "${REQUIRED_PKGS[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING_PKGS+=("$pkg")
done

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    sudo apt-get install -y --no-install-recommends "${MISSING_PKGS[@]}"
    ok "Installed: ${MISSING_PKGS[*]}"
else
    ok "All required system packages are already present."
fi

# ---------- 4. Fetch the bot repository (idempotent) ----------------------
if [[ -d "${PROJECT_DIR}/.git" ]]; then
    log "Repository already present, pulling latest changes..."
    git -C "$PROJECT_DIR" pull --ff-only
else
    log "Cloning repository..."
    git clone "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"
ok "Repository ready at ${PROJECT_DIR}."

# ---------- 5. Python environment (idempotent, cached) --------------------
if [[ ! -d ".venv" ]]; then
    log "Creating virtual environment..."
    python3 -m venv .venv
else
    log "Reusing existing virtual environment."
fi

# shellcheck disable=SC1091
source .venv/bin/activate

log "Upgrading pip..."
pip install --quiet --upgrade pip

log "Installing Python dependencies..."
pip install --quiet -r requirements.txt

log "Installing Playwright browser (Chromium)..."
if ! python3 -m playwright install --dry-run chromium >/dev/null 2>&1; then
    playwright install chromium
else
    ok "Chromium already installed for Playwright."
fi

# ---------- 6. Store credentials securely ---------------------------------
ENV_FILE="${PROJECT_DIR}/.env"
log "Writing credentials to ${ENV_FILE} (not committed to git)..."
umask 077
cat > "$ENV_FILE" <<EOF
TELEGRAM_TOKEN=${TG_TOKEN}
EOF
chmod 600 "$ENV_FILE"

if ! grep -qxF ".env" "${PROJECT_DIR}/.gitignore" 2>/dev/null; then
    echo ".env" >> "${PROJECT_DIR}/.gitignore"
fi
ok "Credentials stored with restricted permissions and excluded from git."

echo
echo "=================================================================="
ok "Setup complete."
echo "=================================================================="
echo

# ---------- 7. Start the bot ------------------------------------------
case "$RUN_MODE" in
    systemd)
        log "Configuring systemd service '${SERVICE_NAME}'..."
        SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
        sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Telegram Bot Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${PROJECT_DIR}/.venv/bin/python cloud_bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable --now "${SERVICE_NAME}.service"
        ok "Service enabled and started."
        echo "  Check status:  systemctl status ${SERVICE_NAME}"
        echo "  View logs:     journalctl -u ${SERVICE_NAME} -f"
        ;;
    screen)
        log "Starting bot in a detached screen session named '${SERVICE_NAME}'..."
        screen -dmS "$SERVICE_NAME" bash -c "cd '${PROJECT_DIR}' && source .venv/bin/activate && set -a && source '${ENV_FILE}' && set +a && python cloud_bot.py"
        ok "Bot running in background."
        echo "  Attach:   screen -r ${SERVICE_NAME}"
        echo "  Detach:   Ctrl+A, then D"
        ;;
    *)
        echo "You can start the bot manually at any time with:"
        echo "  cd ${PROJECT_DIR} && source .venv/bin/activate && set -a && source .env && set +a && python cloud_bot.py"
        ;;
esac

echo
log "Full setup log available at ${LOG_FILE}"
