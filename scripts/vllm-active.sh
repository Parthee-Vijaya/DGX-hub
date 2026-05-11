#!/bin/bash
# ============================================================================
# vllm-active.sh — vLLM-launcher der vælger model baseret på Spark Hub-config
#
# Læser ~/.config/spark-hub/vllm-active.conf (VLLM_MODEL=coder|omni|gptoss)
# og exec'er det matchende start-script fra ai_toolbox/scripts/.
#
# Bruges som ExecStart for ~/.config/systemd/user/vllm.service.
# ============================================================================

set -euo pipefail

CONFIG="$HOME/.config/spark-hub/vllm-active.conf"
TOOLBOX_SCRIPTS="$HOME/Documents/ai_toolbox/ai-toolbox/scripts"

# Default model
VLLM_MODEL="${VLLM_MODEL:-coder}"

# Læs config hvis den findes
if [ -f "$CONFIG" ]; then
  # shellcheck source=/dev/null
  source "$CONFIG"
fi

case "$VLLM_MODEL" in
  coder)
    exec "$TOOLBOX_SCRIPTS/start_qwencoder_server.sh"
    ;;
  omni)
    exec "$TOOLBOX_SCRIPTS/start_omni_server.sh"
    ;;
  gptoss)
    exec "$TOOLBOX_SCRIPTS/start_gptoss_server.sh"
    ;;
  *)
    echo "vllm-active.sh: ukendt VLLM_MODEL=$VLLM_MODEL" >&2
    echo "  tilladte værdier: coder, omni, gptoss" >&2
    exit 1
    ;;
esac
