from .base import DockerInstaller


class JellyfinInstaller(DockerInstaller):
    id = "jellyfin"
    name = "Jellyfin"
    port = 8096
    container_names = ["jellyfin"]
    compose_template = "jellyfin/docker-compose.yml.j2"
    data_subdirs = ["config", "cache"]
