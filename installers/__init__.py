from .base import Installer, InstallContext
from .jellyfin import JellyfinInstaller
from .plex import PlexInstaller
from .ollama import OllamaInstaller
from .immich import ImmichInstaller
from .nextcloud import NextcloudInstaller

REGISTRY: dict[str, Installer] = {
    inst.id: inst
    for inst in [
        JellyfinInstaller(),
        PlexInstaller(),
        OllamaInstaller(),
        ImmichInstaller(),
        NextcloudInstaller(),
    ]
}

__all__ = ["Installer", "InstallContext", "REGISTRY"]
