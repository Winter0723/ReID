"""Check effective restart policies without reading private env or starting Docker services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LONG_RUNNING_SERVICES = {
    "backend", "worker", "maintenance", "visitor-coordinator", "staff-web", "visitor-web",
    "postgres", "redis", "minio", "etcd", "milvus",
}
VARIANTS = (
    ("local", ".env.docker-local.example", ()),
    ("production", ".env.prod.example", ("docker-compose.prod.yml",)),
    ("staging", ".env.staging.example", ("docker-compose.staging.yml",)),
    ("development", ".env.dev.example", ("docker-compose.dev.yml",)),
    ("windows-gpu", ".env.prod.example", ("docker-compose.local-production.yml",)),
    ("visitor-test", ".env.visitor-test.example", ("docker-compose.visitor-test.yml",)),
    ("https", ".env.prod.example", ("docker-compose.prod.yml", "docker-compose.https.yml")),
    ("e2e", ".env.docker-local.example", ("docker-compose.e2e.yml",)),
)


class ComposeRestartContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docker = shutil.which("docker")
        if cls.docker is None:
            raise unittest.SkipTest("Docker CLI is required for Compose contract checks")
        result = subprocess.run([cls.docker, "compose", "version"], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise unittest.SkipTest("Docker Compose plugin is required for contract checks")

    def test_long_running_services_restart_and_one_shot_jobs_do_not(self):
        # Hardcoded env_file paths in GPU/test overrides must resolve to examples,
        # never to the operator's .env.prod or .env.visitor-test in the checkout.
        temporary_root = (ROOT / "tmp").resolve()
        self.assertTrue(temporary_root.is_relative_to(ROOT.resolve()))
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="compose-contract-", dir=temporary_root) as temporary:
            root = Path(temporary)
            self.assertTrue(root.resolve().is_relative_to(temporary_root))
            template_keys = set()
            for source in ROOT.glob("docker-compose*.yml"):
                shutil.copyfile(source, root / source.name)
            for source in ROOT.glob(".env*.example"):
                shutil.copyfile(source, root / source.name)
                for line in source.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith("#") and "=" in line:
                        template_keys.add(line.partition("=")[0].strip())
            for name in (".env.prod", ".env.visitor-test"):
                shutil.copyfile(root / (name + ".example"), root / name)
            environment = {
                key: value for key, value in os.environ.items()
                if key.upper() not in template_keys and not key.upper().startswith("COMPOSE_")
            }
            environment.update(STAFF_DOMAIN="staff.example.test", VISITOR_DOMAIN="visit.example.test",
                               ACME_EMAIL="operator@example.test")

            for label, template, overrides in VARIANTS:
                with self.subTest(variant=label):
                    environment["MYREID_ENV_FILE"] = template
                    command = [self.docker, "compose", "--env-file", template, "-p", "myreid-contract"]
                    for filename in ("docker-compose.yml", *overrides):
                        command.extend(("-f", filename))
                    command.extend(("--profile", "staff", "--profile", "visitor", "config", "--format", "json"))
                    result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                            text=True, encoding="utf-8", timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    services = json.loads(result.stdout)["services"]
                    self.assertTrue(LONG_RUNNING_SERVICES <= services.keys())
                    for name, service in services.items():
                        if name in {"database-init", "e2e"}:
                            self.assertEqual(service.get("restart", "no"), "no", name)
                        else:
                            self.assertEqual(service.get("restart"), "unless-stopped", name)


if __name__ == "__main__":
    unittest.main()
