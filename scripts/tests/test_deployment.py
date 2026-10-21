import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


prepare = load("prepare_deployment")
check = load("check_deployment")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="deployment-test-", dir=ROOT / "tmp")
        self.root = Path(self.temporary.name)
        assert self.root.resolve().is_relative_to((ROOT / "tmp").resolve())
        for name in (".env.prod.example", ".env.docker-local.example"):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        self.root_patch = patch.object(prepare, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temporary.cleanup()

    def generate(self, mode):
        path = self.root / ".env.private"
        prepare.prepare(mode, path)
        return path, check.read_env(path)

    def test_local_has_consistent_credentials_and_no_external_requirement(self):
        path, values = self.generate("local")
        self.assertEqual(check.validate(values), [])
        self.assertEqual(len(values["SECRET_KEY"]), 64)
        self.assertNotEqual(values["POSTGRES_PASSWORD"], values["MINIO_ROOT_PASSWORD"])
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            prepare.prepare("local", path)
        self.assertEqual(before, path.read_bytes())

    def test_production_reports_real_mail_and_oauth_requirements(self):
        _, values = self.generate("production")
        errors = " ".join(check.validate(values))
        self.assertIn("SMTP_HOST", errors)
        self.assertIn("VISITOR_WECHAT_APPID", errors)
        for key in ("BOOTSTRAP_ADMIN_EMAIL", "SMTP_FROM", "SMTP_USERNAME"):
            values[key] = "operator@deployment.test"
        values.update(SMTP_HOST="smtp.deployment.test", SMTP_PASSWORD="synthetic-mail-password")
        self.assertEqual(check.validate(values, staff_only=True), [])
        self.assertTrue(check.validate(values))
        values.update(VISITOR_WECHAT_APPID="synthetic-appid", VISITOR_WECHAT_SECRET="synthetic-secret",
                      VISITOR_PUBLIC_BASE_URL="https://visit.deployment.test")
        self.assertEqual(check.validate(values), [])
        values["VISITOR_AUTH_PROVIDER"] = "test"
        self.assertTrue(check.validate(values))

    def test_mismatched_database_password_and_storage_credentials_fail_without_values(self):
        _, values = self.generate("local")
        values.update(DATABASE_URL="postgresql+psycopg://user:private-password@localhost/db",
                      S3_SECRET_ACCESS_KEY="private-storage-secret")
        errors = " ".join(check.validate(values))
        self.assertIn("DATABASE_URL", errors)
        self.assertIn("S3 credentials", errors)
        self.assertNotIn("private-password", errors)
        self.assertNotIn("private-storage-secret", errors)

    def test_env_references_must_exist_and_duplicate_keys_are_rejected(self):
        path = self.root / "broken.env"
        for source in ("ONE=${MISSING}\n", "ONE=1\nONE=2\n"):
            path.write_text(source)
            with self.assertRaises(ValueError):
                check.read_env(path)


if __name__ == "__main__":
    unittest.main()
