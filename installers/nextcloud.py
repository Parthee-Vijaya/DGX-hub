from .base import DockerInstaller, InstallContext, gen_secret


class NextcloudInstaller(DockerInstaller):
    id = "nextcloud"
    name = "Nextcloud"
    port = 8090
    container_names = ["nextcloud", "nextcloud_db"]
    compose_template = "nextcloud/docker-compose.yml.j2"
    data_subdirs = ["data", "config", "db"]

    def template_vars(self, ctx: InstallContext) -> dict:
        v = super().template_vars(ctx)
        # Persist secrets across re-installs by reading the existing compose
        compose = self.data_dir(ctx) / "docker-compose.yml"
        prev = {"db_password": None, "db_root_password": None}
        if compose.exists():
            text = compose.read_text()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("- MYSQL_PASSWORD=") and prev["db_password"] is None:
                    prev["db_password"] = line.split("=", 1)[1]
                elif line.startswith("- MYSQL_ROOT_PASSWORD="):
                    prev["db_root_password"] = line.split("=", 1)[1]
        v["db_password"] = prev["db_password"] or gen_secret(32)
        v["db_root_password"] = prev["db_root_password"] or gen_secret(32)
        v["admin_user"] = ctx.nextcloud_admin_user or "admin"
        v["admin_password"] = ctx.nextcloud_admin_password or gen_secret(16)
        v["trusted_domains"] = ctx.trusted_domains or "localhost"
        return v
