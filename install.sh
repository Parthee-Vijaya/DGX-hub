#!/usr/bin/env bash
# ============================================================================
#  Spark Hub installer — DGX Spark system monitor dashboard
#
#  curl -fsSL https://raw.githubusercontent.com/Parthee-Vijaya/DGX-hub/main/install.sh | bash
#
#  What this does:
#    1. Installs system deps (python3 venv, git, curl) via apt + sudo
#    2. Clones spark-hub into ~/spark-hub (or updates if already there)
#    3. Creates Python venv and installs requirements
#    4. Enables user-linger so the dashboard can talk to user systemd
#       services (vLLM, LiteLLM) for restart-knapperne
#    5. Generates and installs the systemd unit with your user/paths
#    6. Enables + starts spark-hub on port 7863
#
#  What this dashboard does:
#    - Detailed system monitoring (CPU per-core, GPU, RAM, disk, net, processes)
#    - Restart buttons for vLLM and LiteLLM (user systemd services)
#    - Embedded terminal (iframes web-terminal on port 7862 if present)
# ============================================================================

set -euo pipefail

# --- Configuration ----------------------------------------------------------
SPARK_HUB_REPO="${SPARK_HUB_REPO:-https://github.com/Parthee-Vijaya/DGX-hub.git}"
SPARK_HUB_BRANCH="${SPARK_HUB_BRANCH:-main}"
INSTALL_DIR="${SPARK_HUB_DIR:-$HOME/spark-hub}"
PORT="${SPARK_HUB_PORT:-7863}"

# --- Pretty output ----------------------------------------------------------
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
die()   { echo -e "  ${RED}✗${NC} $*" >&2; exit 1; }

# --- Sanity checks ----------------------------------------------------------
[ "$(id -u)" -eq 0 ] && die "Kør IKKE som root — kør som almindelig bruger; vi bruger sudo hvor nødvendigt."
command -v sudo >/dev/null || die "sudo skal være installeret"
command -v apt-get >/dev/null || die "Denne installer kræver apt (Ubuntu/Debian)"

REAL_USER="$(id -un)"
REAL_HOME="$HOME"
REAL_UID="$(id -u)"

echo ""
echo -e "${BLUE}⚡ Spark Hub installer${NC}"
echo "   bruger:       $REAL_USER (uid $REAL_UID)"
echo "   home:         $REAL_HOME"
echo "   installation: $INSTALL_DIR"
echo "   port:         $PORT"
echo ""

# --- 1. System packages -----------------------------------------------------
step "Installerer system-pakker (python3-venv, git, curl)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates gnupg
ok "System-pakker OK"

# --- 2. Clone or update repo ------------------------------------------------
step "Henter Spark Hub..."
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only --quiet || warn "git pull fejlede — fortsætter med eksisterende kode"
  ok "Opdateret eksisterende installation"
elif [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/app.py" ]; then
  ok "Bruger eksisterende mappe (ingen git)"
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if ! git clone --quiet --branch "$SPARK_HUB_BRANCH" "$SPARK_HUB_REPO" "$INSTALL_DIR" 2>/dev/null; then
    die "Kunne ikke klone $SPARK_HUB_REPO — sæt SPARK_HUB_REPO=<url> og prøv igen"
  fi
  ok "Klonet fra $SPARK_HUB_REPO"
fi

cd "$INSTALL_DIR"

# --- 3. Python venv ---------------------------------------------------------
step "Opretter Python venv..."
if [ ! -d venv ]; then
  python3 -m venv venv
fi
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
ok "Python venv klar"

# --- 4. Linger (så systemctl --user virker fra system-service-konteksten) ---
step "Aktiverer user-linger for $REAL_USER..."
if loginctl show-user "$REAL_USER" 2>/dev/null | grep -q '^Linger=yes'; then
  ok "Linger allerede aktiveret"
else
  sudo loginctl enable-linger "$REAL_USER"
  ok "Linger aktiveret — /run/user/$REAL_UID vil nu altid eksistere"
fi

# --- 5. Static icons (if missing) -------------------------------------------
if [ ! -f static/icon-192.png ]; then
  step "Genererer PWA icons..."
  ./venv/bin/pip install --quiet Pillow >/dev/null 2>&1 && \
  ./venv/bin/python3 -c "
from PIL import Image, ImageDraw
for size in [192, 512]:
    img = Image.new('RGBA', (size, size), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size-1, size-1], radius=size//5, fill=(14, 165, 233))
    cx, cy = size//2, size//2
    s = size * 0.3
    pts = [(cx-s*0.15,cy-s),(cx+s*0.5,cy-s),(cx+s*0.05,cy-s*0.05),(cx+s*0.55,cy-s*0.05),(cx-s*0.2,cy+s),(cx+s*0.05,cy+s*0.1)]
    d.polygon(pts, fill='white')
    img.save(f'static/icon-{size}.png')
" 2>/dev/null && ok "Icons genereret" || warn "Icons sprunget over"
fi

# --- 6. Cleanup gammel sudoers-helper hvis tidligere version blev installeret
if [ -f /etc/sudoers.d/spark-hub ] || [ -f /usr/local/bin/spark-hub-helper ]; then
  step "Fjerner gammel sudoers-helper (ikke længere nødvendig)..."
  sudo rm -f /etc/sudoers.d/spark-hub /usr/local/bin/spark-hub-helper
  ok "Sudoers-helper fjernet"
fi

# --- 7. Systemd unit --------------------------------------------------------
step "Genererer systemd unit..."
UNIT_TMP="$(mktemp)"
sed -e "s|__USER__|$REAL_USER|g" \
    -e "s|__HOME__|$REAL_HOME|g" \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    spark-hub.service.template > "$UNIT_TMP"
sudo install -m 0644 -o root -g root "$UNIT_TMP" /etc/systemd/system/spark-hub.service
rm -f "$UNIT_TMP"
sudo systemctl daemon-reload

# Stop any running ad-hoc instance on the port
if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
  warn "Port $PORT er optaget — stopper det der kører..."
  sudo fuser -k "$PORT/tcp" 2>/dev/null || true
  sleep 1
fi

sudo systemctl enable --now spark-hub
ok "spark-hub.service kører (Restart=always)"

# --- 8. Done ----------------------------------------------------------------
LAN_IP="$(ip -4 addr show scope global 2>/dev/null | grep -v docker | grep -v br- | grep -v tailscale | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)"
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"

echo ""
echo "════════════════════════════════════════════════════"
echo -e "${GREEN}⚡ Spark Hub system-monitor er installeret og kører${NC}"
echo ""
echo -e "  Lokalt:    ${BLUE}http://${LAN_IP:-localhost}:${PORT}${NC}"
[ -n "$TS_IP" ] && echo -e "  Tailscale: ${BLUE}http://${TS_IP}:${PORT}${NC}"
echo ""
echo "  Features:"
echo "    - Detaljeret system-monitor (CPU per-core, GPU, RAM, disk, net)"
echo "    - Restart-knapper for vLLM og LiteLLM (user systemd services)"
echo "    - Indlejret terminal (kræver web-terminal på port 7862)"
echo ""
echo "  Kommandoer:"
echo "    sudo systemctl status spark-hub       # tjek status"
echo "    sudo systemctl restart spark-hub      # genstart"
echo "    sudo journalctl -u spark-hub -f       # følg log"
echo "════════════════════════════════════════════════════"
