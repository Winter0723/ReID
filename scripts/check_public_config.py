"""Check example completeness without loading any private environment file."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    tree = ast.parse((ROOT / "FastAPIBackend/app/core/config.py").read_text(encoding="utf-8"))
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    required = {node.target.id.upper() for node in settings.body if isinstance(node, ast.AnnAssign)}
    failures = []
    for path in sorted(ROOT.glob(".env*.example")):
        keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", path.read_text(encoding="utf-8"), re.M)
        missing = sorted(required - set(keys))
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if missing or duplicates:
            failures.append(f"{path.name}: missing={missing}, duplicate={duplicates}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"All example files cover {len(required)} backend settings with no duplicate keys.")


if __name__ == "__main__":
    main()
