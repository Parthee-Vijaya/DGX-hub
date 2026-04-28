#!/bin/bash
# ============================================================
#  Spark Hub — One-line installer for DGX Spark
#  Usage: curl -fsSL <raw-url>/install.sh | bash
# ============================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${BLUE}⚡ Spark Hub Installer${NC}"
echo "======================================"
echo ""

INSTALL_DIR="$HOME/Documents/spark-hub"
CONFIG_PATH="$HOME/spark-hub-config.json"

# --- Check prerequisites ---
echo -e "${BLUE}[1/6]${NC} Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ Python3 not found. Install it first.${NC}"
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo -e "${YELLOW}⚠ Docker not found. Services like Jellyfin/Immich won't work.${NC}"
fi

echo -e "${GREEN}✓${NC} Prerequisites OK"

# --- Clone or update repo ---
echo -e "${BLUE}[2/6]${NC} Setting up Spark Hub..."

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    if [ -d "$INSTALL_DIR" ]; then
        echo "  Directory exists but not a git repo — backing up..."
        mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.$(date +%s)"
    fi
    echo "  Cloning repository..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone https://github.com/$(git config --global user.name 2>/dev/null || echo 'user')/spark-hub.git "$INSTALL_DIR" 2>/dev/null || {
        echo -e "${YELLOW}⚠ Could not clone from GitHub. Using local files.${NC}"
    }
fi

cd "$INSTALL_DIR"

# --- Python venv ---
echo -e "${BLUE}[3/6]${NC} Setting up Python environment..."

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install -q -r requirements.txt

echo -e "${GREEN}✓${NC} Python environment ready"

# --- Generate icons if missing ---
if [ ! -f "static/icon-192.png" ]; then
    echo -e "${BLUE}[3.5/6]${NC} Generating app icons..."
    ./venv/bin/pip install -q Pillow
    ./venv/bin/python3 -c "
from PIL import Image, ImageDraw
for size in [192, 512]:
    img = Image.new('RGBA', (size, size), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size-1, size-1], radius=size//5, fill=(14, 165, 233))
    cx, cy = size//2, size//2
    s = size * 0.3
    points = [(cx-s*0.15,cy-s),(cx+s*0.5,cy-s),(cx+s*0.05,cy-s*0.05),(cx+s*0.55,cy-s*0.05),(cx-s*0.2,cy+s),(cx+s*0.05,cy+s*0.1)]
    d.polygon(points, fill='white')
    img.save(f'static/icon-{size}.png')
" 2>/dev/null && echo -e "${GREEN}✓${NC} Icons generated" || echo -e "${YELLOW}⚠ Icon generation skipped (Pillow not available)${NC}"
fi

# --- Docker services ---
echo -e "${BLUE}[4/6]${NC} Setting up Docker services..."

# Jellyfin
if [ ! -f "$HOME/jellyfin/docker-compose.yml" ]; then
    mkdir -p "$HOME/jellyfin/config" "$HOME/jellyfin/cache"
    mkdir -p "$HOME/Media/film" "$HOME/Media/serier"
    cp "$INSTALL_DIR/docker/jellyfin/docker-compose.yml" "$HOME/jellyfin/" 2>/dev/null || true
    echo -e "${GREEN}✓${NC} Jellyfin configured"
fi

# Immich
if [ ! -f "$HOME/immich/docker-compose.yml" ]; then
    mkdir -p "$HOME/immich/data/library" "$HOME/immich/data/model-cache" "$HOME/immich/data/postgres"
    cp "$INSTALL_DIR/docker/immich/docker-compose.yml" "$HOME/immich/" 2>/dev/null || true
    cp "$INSTALL_DIR/docker/immich/.env" "$HOME/immich/" 2>/dev/null || true
    echo -e "${GREEN}✓${NC} Immich configured"
fi

# Nextcloud
if [ ! -f "$HOME/nextcloud/docker-compose.yml" ]; then
    mkdir -p "$HOME/nextcloud/data" "$HOME/nextcloud/config" "$HOME/nextcloud/db"
    cp "$INSTALL_DIR/docker/nextcloud/docker-compose.yml" "$HOME/nextcloud/" 2>/dev/null || true
    echo -e "${GREEN}✓${NC} Nextcloud configured"
fi

# Start Docker services
if command -v docker &>/dev/null; then
    echo "  Starting Docker services..."
    docker compose -f "$HOME/jellyfin/docker-compose.yml" up -d 2>/dev/null || true
    docker compose -f "$HOME/immich/docker-compose.yml" up -d 2>/dev/null || true
    docker compose -f "$HOME/nextcloud/docker-compose.yml" up -d 2>/dev/null || true
    echo -e "${GREEN}✓${NC} Docker services started"
fi

# --- Systemd service ---
echo -e "${BLUE}[5/6]${NC} Installing systemd service..."

if [ -f "$INSTALL_DIR/spark-hub.service" ]; then
    sudo cp "$INSTALL_DIR/spark-hub.service" /etc/systemd/system/ 2>/dev/null && \
    sudo systemctl daemon-reload 2>/dev/null && \
    sudo systemctl enable --now spark-hub 2>/dev/null && \
    echo -e "${GREEN}✓${NC} Spark Hub service installed and started" || \
    echo -e "${YELLOW}⚠ Could not install systemd service (needs sudo). Start manually: cd $INSTALL_DIR && ./start.sh${NC}"
else
    echo -e "${YELLOW}⚠ Service file not found. Start manually: cd $INSTALL_DIR && ./start.sh${NC}"
fi

# --- Done ---
echo -e "${BLUE}[6/6]${NC} Finding network address..."

LAN_IP=$(ip -4 addr show scope global | grep -v docker | grep -v br- | grep -v tailscale | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)

echo ""
echo "======================================"
echo -e "${GREEN}⚡ Spark Hub er installeret!${NC}"
echo ""
echo -e "  Dashboard:  ${BLUE}http://${LAN_IP:-localhost}:7863${NC}"
echo -e "  Jellyfin:   http://${LAN_IP:-localhost}:8096"
echo -e "  Immich:     http://${LAN_IP:-localhost}:2283"
echo -e "  Nextcloud:  http://${LAN_IP:-localhost}:8080"
echo ""
echo "  Åbn dashboardet i Safari/Chrome og"
echo "  tilføj til hjemmeskærm for app-oplevelse."
echo "======================================"
echo ""
