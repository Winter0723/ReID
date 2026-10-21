"""Reject wildcard imports from app.api.common and suggest exact symbols."""

from __future__ import annotations

import argparse
import ast
import builtins
import symtable
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
COMMON_PATH = BACKEND_DIR / "app" / "api" / "common.py"


def _bound_names(source: str, filename: str) -> set[str]:
    table = symtable.symtable(source, filename, "exec")
    return {
        name
        for name in table.get_identifiers()
        if (
            table.lookup(name).is_assigned()
            or table.lookup(name).is_imported()
            or table.lookup(name).is_namespace()
        )
    }


def _referenced_globals(source: str, filename: str) -> set[str]:
    root = symtable.symtable(source, filename, "exec")
    referenced: set[str] = set()

    def visit(table: symtable.SymbolTable) -> None:
        for name in table.get_identifiers():
            symbol = table.lookup(name)
            if symbol.is_referenced() and symbol.is_global():
                referenced.add(name)
        for child in table.get_children():
            visit(child)

    visit(root)
    return referenced


def _common_names() -> set[str]:
    source = COMMON_PATH.read_text(encoding="utf-8")
    return _bound_names(source, str(COMMON_PATH))


def _wildcard_common_imports(path: Path) -> list[ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.api.common"
        and any(alias.name == "*" for alias in node.names)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suggest", action="store_true", help="Print the exact likely import list.")
    args = parser.parse_args()

    common_names = _common_names()
    failures: list[Path] = []
    for path in sorted((BACKEND_DIR / "app" / "api").rglob("*.py")):
        if not _wildcard_common_imports(path):
            continue
        failures.append(path)
        print(path.relative_to(ROOT_DIR).as_posix())
        if args.suggest:
            source = path.read_text(encoding="utf-8")
            unresolved = (
                _referenced_globals(source, str(path))
                - _bound_names(source, str(path))
                - set(dir(builtins))
            )
            print("  " + ", ".join(sorted(unresolved & common_names)))

    if failures:
        print(f"Found {len(failures)} wildcard import(s) from app.api.common")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
