"""Base classes and helpers for service installers.

Each installer implements detect/install/start/stop and yields log lines
during install so the wizard can stream progress via SSE.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "docker"

_jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)

HELPER_BIN = "/usr/local/bin/spark-hub-helper"


@dataclass
class InstallContext:
    """Per-install user choices passed to every installer."""
    home: str
    uid: int
    gid: int
    tz: str = "Europe/Copenhagen"
    media_root: str = ""
    nextcloud_admin_user: str = "admin"
    nextcloud_admin_password: str = ""
    trusted_domains: str = "localhost"
    extras: dict = field(default_factory=dict)


def gen_secret(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def render(template_relpath: str, **vars) -> str:
    """Render a Jinja template under docker/ and return the text."""
    tmpl = _jinja.get_template(template_relpath)
    return tmpl.render(**vars)


async def run_stream(cmd: list[str] | str, cwd: str | None = None,
                     env: dict | None = None) -> AsyncIterator[str]:
    """Run a command and yield combined stdout/stderr lines.

    Final yielded line is `__exit__:<code>` so the caller knows the rc.
    """
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            yield line
    rc = await proc.wait()
    yield f"__exit__:{rc}"


async def run_capture(cmd: list[str] | str, timeout: float = 10.0) -> tuple[int, str]:
    """Run a command and capture combined output."""
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timeout"
    return proc.returncode or 0, out.decode(errors="replace")


def have_cmd(name: str) -> bool:
    return shutil.which(name) is not None


class Installer:
    """Base class for a service installer.

    Subclasses set ``id``, ``name``, ``port``, and implement the async
    methods. ``install`` and friends yield human-readable log lines.
    """

    id: str = ""
    name: str = ""
    port: int = 0
    requires_docker: bool = False
    requires_helper: bool = False  # needs sudoers helper for system-level install

    async def detect(self) -> dict:
        """Return {installed: bool, running: bool, version: str|None}."""
        raise NotImplementedError

    async def install(self, ctx: InstallContext) -> AsyncIterator[str]:
        """Install the service, yielding log lines."""
        raise NotImplementedError
        yield ""  # for type checker — make this an async generator

    async def start(self) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""

    async def stop(self) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""

    async def restart(self) -> AsyncIterator[str]:
        async for line in self.stop():
            yield line
        async for line in self.start():
            yield line


# ---------------------------------------------------------------------------
# Docker-installer mixin — used by jellyfin/immich/nextcloud
# ---------------------------------------------------------------------------

class DockerInstaller(Installer):
    """Common implementation for services installed via docker compose."""

    requires_docker = True
    container_names: list[str] = []     # container names to check for "running"
    compose_template: str = ""           # e.g. "jellyfin/docker-compose.yml.j2"
    extra_files: dict[str, str] = {}     # e.g. {"immich/.env.j2": ".env"}
    data_subdirs: list[str] = []         # subdirs to mkdir under data_dir

    def data_dir(self, ctx: InstallContext) -> Path:
        return Path(ctx.home) / self.id

    def template_vars(self, ctx: InstallContext) -> dict:
        return {
            "home": ctx.home,
            "uid": ctx.uid,
            "gid": ctx.gid,
            "tz": ctx.tz,
            "media_root": ctx.media_root or f"{ctx.home}/Media",
            "data_dir": str(self.data_dir(ctx)),
        }

    async def detect(self) -> dict:
        compose = self.data_dir_path() / "docker-compose.yml"
        installed = compose.exists()
        running = False
        version = None
        if installed and self.container_names:
            rc, out = await run_capture(
                ["docker", "ps", "--format", "{{.Names}}"], timeout=5.0
            )
            if rc == 0:
                running_set = set(out.split())
                running = all(n in running_set for n in self.container_names)
        return {"installed": installed, "running": running, "version": version}

    def data_dir_path(self) -> Path:
        # Plain HOME-based location; resolved at runtime via env (used for detect)
        return Path(os.environ.get("HOME", "/root")) / self.id

    async def install(self, ctx: InstallContext) -> AsyncIterator[str]:
        if not have_cmd("docker"):
            yield "ERROR: docker is not installed. Install docker first."
            yield "__exit__:1"
            return

        data_dir = self.data_dir(ctx)
        for sub in self.data_subdirs:
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        yield f"→ Sat data-mappe op: {data_dir}"

        # Render compose
        vars = self.template_vars(ctx)
        compose_text = render(self.compose_template, **vars)
        (data_dir / "docker-compose.yml").write_text(compose_text)
        yield f"→ Genereret docker-compose.yml ({len(compose_text)} bytes)"

        # Render extra files (e.g. .env)
        for tmpl, dest in self.extra_files.items():
            text = render(tmpl, **vars)
            (data_dir / dest).write_text(text)
            yield f"→ Genereret {dest}"

        yield "→ Henter Docker images (kan tage et par minutter)..."
        async for line in run_stream(
            ["docker", "compose", "up", "-d"], cwd=str(data_dir)
        ):
            yield line

    async def start(self) -> AsyncIterator[str]:
        d = self.data_dir_path()
        async for line in run_stream(
            ["docker", "compose", "up", "-d"], cwd=str(d)
        ):
            yield line

    async def stop(self) -> AsyncIterator[str]:
        d = self.data_dir_path()
        async for line in run_stream(
            ["docker", "compose", "down"], cwd=str(d)
        ):
            yield line

    async def restart(self) -> AsyncIterator[str]:
        d = self.data_dir_path()
        async for line in run_stream(
            ["docker", "compose", "restart"], cwd=str(d)
        ):
            yield line
