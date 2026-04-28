from typing import AsyncIterator

from .base import DockerInstaller, InstallContext, gen_secret


class ImmichInstaller(DockerInstaller):
    id = "immich"
    name = "Immich"
    port = 2283
    container_names = [
        "immich_server",
        "immich_machine_learning",
        "immich_redis",
        "immich_postgres",
    ]
    compose_template = "immich/docker-compose.yml.j2"
    extra_files = {"immich/.env.j2": ".env"}
    data_subdirs = ["data/library", "data/model-cache", "data/postgres"]

    def template_vars(self, ctx: InstallContext) -> dict:
        v = super().template_vars(ctx)
        # Immich data lives under data_dir/data/* — match existing layout
        v["data_dir"] = f"{v['data_dir']}/data"
        # Persist the DB password across re-installs by checking existing .env
        env_file = self.data_dir(ctx) / ".env"
        existing_pw = None
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DB_PASSWORD="):
                    existing_pw = line.split("=", 1)[1].strip()
                    break
        v["db_password"] = existing_pw or gen_secret(32)
        return v
