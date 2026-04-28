#!/bin/bash
# spark-hub-helper — privileged actions whitelisted for the spark-hub user.
#
# Installed at /usr/local/bin/spark-hub-helper with mode 0755 (owned by root).
# /etc/sudoers.d/spark-hub allows NOPASSWD invocation of THIS script only.
# All actions are validated against a fixed allow-list below; unknown actions
# are rejected before any privileged work happens.

set -euo pipefail

ACTION="${1:-}"
shift || true

case "$ACTION" in
  install-plex)
    apt-get update -qq
    if ! command -v wget >/dev/null; then apt-get install -y wget; fi
    if [ ! -f /etc/apt/sources.list.d/plexmediaserver.list ]; then
      curl -fsSL https://downloads.plex.tv/plex-keys/PlexSign.key \
        | gpg --dearmor -o /usr/share/keyrings/plex-archive-keyring.gpg
      echo "deb [signed-by=/usr/share/keyrings/plex-archive-keyring.gpg] https://downloads.plex.tv/repo/deb public main" \
        > /etc/apt/sources.list.d/plexmediaserver.list
      apt-get update -qq
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y plexmediaserver
    systemctl enable --now plexmediaserver
    ;;

  fix-plex-perms)
    # Add plex user to the calling user's group so it can read media files.
    REAL_USER="${SUDO_USER:-}"
    if [ -n "$REAL_USER" ]; then
      usermod -a -G "$REAL_USER" plex || true
      systemctl restart plexmediaserver || true
    fi
    ;;

  start-plex)   systemctl start plexmediaserver ;;
  stop-plex)    systemctl stop plexmediaserver ;;
  restart-plex) systemctl restart plexmediaserver ;;

  install-ollama)
    # Official one-line installer — installs ollama systemd unit.
    curl -fsSL https://ollama.com/install.sh | sh
    ;;

  start-ollama)   systemctl start ollama ;;
  stop-ollama)    systemctl stop ollama ;;
  restart-ollama) systemctl restart ollama ;;

  *)
    echo "spark-hub-helper: ukendt kommando '$ACTION'" >&2
    echo "tilladte: install-plex, fix-plex-perms, start-plex, stop-plex, restart-plex," >&2
    echo "          install-ollama, start-ollama, stop-ollama, restart-ollama" >&2
    exit 64
    ;;
esac
