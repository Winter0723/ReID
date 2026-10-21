"""Release rules must reject private data without rejecting safe example names."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "export_public_source.py"
spec = importlib.util.spec_from_file_location("public_export", SCRIPT)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class PublicReleaseTests(unittest.TestCase):
    def test_complete_examples_are_allowed(self):
        for name in (".env.example", ".env.sqlite.example", ".env.prod.example"):
            self.assertTrue(export.allowed(name), name)

    def test_private_files_and_history_are_rejected(self):
        for name in (".env.prod", ".env.backup", ".git/config", "FastAPIBackend/reid.db",
                     "FastAPIBackend/object_storage/picture.jpg", "TauriFrontend/.vscode/settings.json",
                     "model/weights.pth", "model/model.onnx", "model/model.engine",
                     "FastAPIBackend/recording.mp4", "private.key", "backups/snapshot.tar"):
            self.assertFalse(export.allowed(name), name)

    def test_scanner_does_not_match_its_own_rules(self):
        export.check_text(SCRIPT.name, SCRIPT.read_bytes())

    def test_personal_paths_and_private_key_blocks_are_rejected(self):
        for value in (b'C:\\Users\\' + b'private-user\\repo', b'/' + b'Users/private-user/repo',
                      b'-----BEGIN ' + b'PRIVATE KEY-----'):
            with self.assertRaises(ValueError):
                export.check_text("example.txt", value)

    def test_directory_and_archive_share_verified_source_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "README.md").write_bytes(b"Source distribution\r\n")
            (root / "scripts").mkdir()
            (root / "scripts/start.sh").write_bytes(b"#!/bin/sh\r\nexit 0\r\n")
            archive_path, directory = root / "source.zip", root / "ReID"
            argv = ["export_public_source.py", "--directory", str(directory), "--output", str(archive_path)]
            with patch.object(export, "ROOT", root), patch.object(export.subprocess, "check_output", return_value=b"README.md\0scripts/start.sh\0"), patch("sys.argv", argv):
                export.main()
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read("SOURCE_MANIFEST.json"))
                self.assertEqual(archive.read("SOURCE_MANIFEST.json"), (directory / "SOURCE_MANIFEST.json").read_bytes())
                self.assertEqual(archive.read("README.md"), b"Source distribution\n")
                self.assertEqual((archive.getinfo("scripts/start.sh").external_attr >> 16) & 0o777, 0o755)
                self.assertEqual(manifest, json.loads((directory / "SOURCE_MANIFEST.json").read_text()))
                for entry in manifest:
                    data = (directory / entry["path"]).read_bytes()
                    self.assertEqual(data, archive.read(entry["path"]))
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])

    def test_portable_line_endings_preserve_binary_data(self):
        self.assertEqual(export.portable_bytes(b"#!/bin/sh\r\nexit 0\r\n"), b"#!/bin/sh\nexit 0\n")
        for data in (b"\x00\r\n", b"\xff\r\n"):
            self.assertEqual(export.portable_bytes(data), data)


if __name__ == "__main__":
    unittest.main()
