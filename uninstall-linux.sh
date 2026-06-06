#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  Hermes Phone — Linux/WSL2 Uninstaller                          ║
# ╚══════════════════════════════════════════════════════════════════╝
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="$HOME/.hermes/phone-agent"
SERVICE_NAME="com.janlabs.phone-agent"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  📞 Hermes Phone — Linux/WSL2 Uninstaller                   ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Stop and disable service ───────────────────────────────────────
echo -e "${YELLOW}Stopping service...${NC}"

if systemctl --user is-active "$SERVICE_NAME.service" &>/dev/null; then
    systemctl --user stop "$SERVICE_NAME.service"
    echo -e "${GREEN}  ✅ Service stopped${NC}"
else
    echo -e "  ⚠️  Service was not running"
fi

if systemctl --user is-enabled "$SERVICE_NAME.service" &>/dev/null; then
    systemctl --user disable "$SERVICE_NAME.service" 2>/dev/null || true
    echo -e "${GREEN}  ✅ Service disabled${NC}"
fi

# ── Remove systemd service file ────────────────────────────────────
echo ""
echo -e "${YELLOW}Removing systemd service...${NC}"

if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
    echo -e "${GREEN}  ✅ Service file removed${NC}"
else
    echo -e "  ⚠️  Service file not found"
fi

# ── Ask about data ─────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Data directory: $INSTALL_DIR${NC}"
echo ""
read -p "Remove voicemails, config, and venv? (y/N): " REMOVE_DATA

if [[ "$REMOVE_DATA" =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo -e "${GREEN}  ✅ All data removed${NC}"
else
    # Always remove venv to save space, but keep config and voicemails
    if [[ -d "$INSTALL_DIR/venv" ]]; then
        read -p "  Remove virtual environment (saves disk space)? (y/N): " REMOVE_VENV
        if [[ "$REMOVE_VENV" =~ ^[Yy]$ ]]; then
            rm -rf "$INSTALL_DIR/venv"
            echo -e "${GREEN}  ✅ Virtual environment removed${NC}"
        fi
    fi
    echo -e "${CYAN}  ℹ️  Data preserved at $INSTALL_DIR${NC}"
    echo -e "${CYAN}  ℹ️  Config: $INSTALL_DIR/.env${NC}"
    echo -e "${CYAN}  ℹ️  Voicemails: $INSTALL_DIR/voicemails/${NC}"
fi

# ── Done ───────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}✅ Hermes Phone uninstalled${NC}"
echo ""
echo -e "To reinstall: ./install-linux.sh"
echo ""
