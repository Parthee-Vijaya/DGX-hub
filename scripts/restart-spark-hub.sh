#!/bin/bash
# Genstarter spark-hub.service. `systemctl restart` starter også servicen
# hvis den ikke kører — så ét klik dækker begge tilfælde.

set -u

if pkexec systemctl restart spark-hub.service; then
  notify-send -i /home/dgx-3/Documents/spark-hub/static/icon-192.png \
    "Spark Hub" "Service genstartet — http://localhost:7863"
else
  notify-send -u critical \
    -i /home/dgx-3/Documents/spark-hub/static/icon-192.png \
    "Spark Hub" "Genstart fejlede (afbrudt eller forkert kodeord)"
  exit 1
fi
