"""Ollama installer.

Installs ollama via the official systemd-installing script through the
sudoers-allowed helper, then pulls a default chat model.
"""
import os
from typing import AsyncIterator

from .base import (
    HELPER_BIN,
    Installer,
    InstallContext,
    have_cmd,
    run_capture,
    run_stream,
)

DEFAULT_MODEL = "mistral-small3.2:latest"


class OllamaInstaller(Installer):
    id = "ollama"
    name = "Ollama"
    port = 11434
    requires_helper = True

    async def detect(self) -> dict:
        installed = have_cmd("ollama")
        running = False
        version = None
        if installed:
            rc, out = await run_capture(["ollama", "--version"], timeout=5.0)
            if rc == 0:
                version = out.strip().splitlines()[0] if out.strip() else None
            rc, out = await run_capture(
                ["systemctl", "is-active", "ollama"], timeout=5.0
            )
            running = out.strip() == "active"
        return {"installed": installed, "running": running, "version": version}

    async def install(self, ctx: InstallContext) -> AsyncIterator[str]:
        if not _helper_available():
            yield (
                "ERROR: spark-hub-helper mangler. "
                "Kør install.sh igen som almindelig bruger med sudo-adgang."
            )
            yield "__exit__:1"
            return

        if not have_cmd("ollama"):
            yield "→ Installerer Ollama (henter ~50MB)..."
            rc_seen = 0
            async for line in run_stream(["sudo", "-n", HELPER_BIN, "install-ollama"]):
                yield line
                if line.startswith("__exit__:"):
                    rc_seen = int(line.split(":", 1)[1])
            if rc_seen != 0:
                return
        else:
            yield "→ Ollama er allerede installeret"

        model = ctx.extras.get("ollama_model", DEFAULT_MODEL)
        yield f"→ Henter default model {model} (kan tage flere minutter)..."
        async for line in run_stream(["ollama", "pull", model]):
            yield line

    async def start(self) -> AsyncIterator[str]:
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "start-ollama"]):
            yield line

    async def stop(self) -> AsyncIterator[str]:
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "stop-ollama"]):
            yield line

    async def restart(self) -> AsyncIterator[str]:
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "restart-ollama"]):
            yield line


def _helper_available() -> bool:
    return os.path.exists(HELPER_BIN) and os.access(HELPER_BIN, os.X_OK)
