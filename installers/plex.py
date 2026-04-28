"""Plex Media Server installer.

Uses the system package via a small sudoers-allowed helper script
(see scripts/spark-hub-helper.sh installed by install.sh).
"""
from typing import AsyncIterator

from .base import (
    HELPER_BIN,
    Installer,
    InstallContext,
    have_cmd,
    run_capture,
    run_stream,
)


class PlexInstaller(Installer):
    id = "plex"
    name = "Plex"
    port = 32400
    requires_helper = True

    async def detect(self) -> dict:
        installed = have_cmd("plexmediaserver") or _service_exists("plexmediaserver")
        running = False
        version = None
        if installed:
            rc, out = await run_capture(
                ["systemctl", "is-active", "plexmediaserver"], timeout=5.0
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

        yield "→ Installerer Plex Media Server (kræver kort sudo via helper)..."
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "install-plex"]):
            yield line

        yield "→ Tilføjer Plex til media-gruppen så den kan læse fra Media-mappen..."
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "fix-plex-perms"]):
            yield line

    async def start(self) -> AsyncIterator[str]:
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "start-plex"]):
            yield line

    async def stop(self) -> AsyncIterator[str]:
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "stop-plex"]):
            yield line

    async def restart(self) -> AsyncIterator[str]:
        async for line in run_stream(["sudo", "-n", HELPER_BIN, "restart-plex"]):
            yield line


def _service_exists(unit: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(
            ["systemctl", "list-unit-files", f"{unit}.service"],
            capture_output=True, text=True, timeout=3,
        )
        return unit in r.stdout
    except Exception:
        return False


def _helper_available() -> bool:
    import os
    return os.path.exists(HELPER_BIN) and os.access(HELPER_BIN, os.X_OK)
