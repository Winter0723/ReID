"""Export an audited source snapshot without Git history or local runtime data."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"README.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md",
              ".gitignore", ".gitattributes", ".gitleaks.toml", ".dockerignore", ".editorconfig", "pytest.ini", "SOURCE_MANIFEST.json"}
ROOT_DIRS = {"FastAPIBackend", "TauriFrontend", "VisitorFrontend", "model", "scripts", "docs", "legacy", ".github", "camera_gateway"}
DENIED_PARTS = {".git", ".vscode", ".idea", ".agents", ".codex", "node_modules", "venv", ".venv",
                "gallery", "object_storage", "secrets", "backups", "tmp", ".tmp", "dist", "target",
                "__pycache__", "test-results", "playwright-report"}
DENIED_SUFFIXES = {".pt", ".pth", ".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3",
                   ".sql", ".dump", ".log", ".stdout", ".stderr", ".bak",
                   ".onnx", ".engine", ".safetensors", ".mp4", ".avi", ".mov", ".mkv"}
SYNTHETIC_FIXTURES = {
    "VisitorFrontend/e2e/fixtures/preview.mp4": "fbada00d020cd34a68a07fdd5f14aaae6ba1e6856dd0532a2d11fd2ca1986553",
}


def allowed(name):
    if name in SYNTHETIC_FIXTURES:
        return True
    path = PurePosixPath(name)
    if any(p in DENIED_PARTS for p in path.parts):
        return False
    if path.name.startswith(".env"):
        return path.name.endswith(".example")
    if any(s in DENIED_SUFFIXES for s in path.suffixes):
        return False
    return (name in ROOT_FILES or path.parts[0] in ROOT_DIRS or
            (len(path.parts) == 1 and path.name.startswith("docker-compose") and path.suffix == ".yml"))


def check_text(name, data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return
    patterns = [r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                r"gh[pousr]_[A-Za-z0-9]{30,}", r"AKIA[A-Z0-9]{16}",
                r"C:\\[U]sers\\(?!Public\\|<)[^\\\s]+\\", r"/[U]sers/(?!<|example/)[^/\s]+/"]
    if any(re.search(pattern, text) for pattern in patterns):
        raise ValueError(f"Sensitive material or personal path detected: {name}")


def portable_bytes(data):
    """Use LF for UTF-8 source so Windows exports also run on Linux."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "release" / "myreid-source.zip")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--directory", type=Path, help="Also create a complete source tree in a new directory")
    args = parser.parse_args()
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    files = []
    for name in sorted(filter(None, names)):
        if not allowed(name):
            if Path(name).suffix not in {".pt", ".pth"}:
                raise ValueError(f"Disallowed tracked path: {name}")
            continue
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or symlinked source: {name}")
        data = path.read_bytes()
        if name in SYNTHETIC_FIXTURES and hashlib.sha256(data).hexdigest() != SYNTHETIC_FIXTURES[name]:
            raise ValueError(f"Synthetic test fixture checksum mismatch: {name}")
        if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise ValueError(f"Unresolved LFS object: {name}")
        check_text(name, data)
        files.append((name, portable_bytes(data)))
    if args.check:
        print(f"Validated {len(files)} source files. History is intentionally excluded.")
        return
    if args.directory and args.directory.exists():
        raise ValueError("Source directory already exists; use a new directory")
    if args.output.exists():
        raise ValueError("Archive already exists; use a new filename")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for name, data in files:
            if name == "SOURCE_MANIFEST.json":
                continue  # Regenerate the manifest for this snapshot.
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            manifest.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        archive.writestr("SOURCE_MANIFEST.json", json.dumps(manifest, indent=2))
    if args.directory:
        args.directory.mkdir(parents=True, exist_ok=False)
        for name, data in files:
            if name == "SOURCE_MANIFEST.json":
                continue
            target = args.directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if name.endswith(".sh"):
                target.chmod(0o755)
        (args.directory / "SOURCE_MANIFEST.json").write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    print(f"Exported {len(files)} source files to {args.output}")


if __name__ == "__main__":
    main()
