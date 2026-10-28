"""Explicitly attest historical DSCL-ReID provenance before reusing vectors.

Default is a read-only preview. Stop API, cameras and workers before --apply.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FastAPIBackend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["core", "scenic"], required=True)
    parser.add_argument("--project-id", type=int, action="append", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--expected-sha256", required=True, help="Independently verified original checkpoint hash")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.services.model_registry import bind_project, resolve_model
    from app.services.project_models import database_for
    snapshot = resolve_model(args.model_id)
    if snapshot["adapter"] != "clipreid" or snapshot["sha256"] != args.expected_sha256:
        parser.error("Only a verified original DSCL-ReID checkpoint can claim legacy vectors")
    module = database_for(args.scope)
    with module.SessionLocal() as db:
        projects = [db.get(module.Project, pid) for pid in args.project_id]
        if any(p is None or p.embedding_space_id for p in projects):
            parser.error("All projects must exist and be unbound; existing bindings cannot change")
        print(json.dumps({"projects": args.project_id, "scope": args.scope, "model": snapshot,
                          "apply": args.apply}, indent=2, ensure_ascii=False))
        if args.apply:
            for project in projects:
                bind_project(project, args.model_id)
                snapshot = json.loads(project.model_snapshot_json)
                snapshot["legacy_vectors"] = True
                project.model_snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            db.commit()


if __name__ == "__main__":
    main()
