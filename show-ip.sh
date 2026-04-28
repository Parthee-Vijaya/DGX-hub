#!/bin/bash
# Vis Spark Hub adresse via desktop notification + terminal
sleep 10  # vent på netværk

LAN_IP=$(ip -4 addr show scope global | grep -v docker | grep -v br- | grep -v tailscale | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
TS_IP=$(tailscale ip -4 2>/dev/null)
PORT=7863

MSG="Spark Hub er klar!

LAN:       http://${LAN_IP:-?.?.?.?}:${PORT}
Tailscale: http://${TS_IP:-offline}:${PORT}"

echo "$MSG"

# Desktop notification hvis display er tilgængeligt
if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
    notify-send -i /home/dgx-3/Documents/spark-hub/static/icon-192.png "Spark Hub" "$MSG" 2>/dev/null
fi

# Skriv til fil så det kan hentes via API
echo "{\"lan\": \"${LAN_IP}\", \"tailscale\": \"${TS_IP}\", \"port\": ${PORT}}" > /tmp/spark-hub-ip.json
