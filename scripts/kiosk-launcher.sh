#!/bin/bash
# Venter på spark-hub er klar, lukker eventuelt eksisterende firefox,
# og åbner Firefox i kiosk-mode mod localhost:7863.

set -u
URL="http://localhost:7863"
LOG="$HOME/.cache/spark-hub-kiosk.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "=== $(date -Is) kiosk-launcher start ==="

# Vent op til 60 sek på at spark-hub svarer (servicen kan tage et øjeblik)
for i in $(seq 1 30); do
  if curl -sf --max-time 2 "$URL" >/dev/null; then
    echo "spark-hub klar efter ${i}x2s"
    break
  fi
  sleep 2
done

# Brug en dedikeret Firefox-profil så normal browsing ikke blandes ind
PROFILE_DIR="$HOME/.mozilla/firefox/kiosk"
if [ ! -d "$PROFILE_DIR" ]; then
  firefox -CreateProfile "kiosk $PROFILE_DIR" -no-remote
fi

# Sluk evt. tidligere kiosk-instans før vi starter en ny
pkill -f "firefox.*-P kiosk" 2>/dev/null || true
sleep 1

exec firefox --no-remote -P kiosk --kiosk "$URL"
