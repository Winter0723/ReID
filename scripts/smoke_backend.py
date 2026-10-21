from pathlib import Path
import argparse
import json
import os
import sys

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)
os.environ.setdefault("EMAIL_CODE_DEV_MODE", "1")

import main
import database


def parse_args():
    parser = argparse.ArgumentParser(description="Run an in-process MyReID backend smoke test.")
    parser.add_argument("--username", default=os.getenv("MYREID_SMOKE_USER", "smoke_user"))
    parser.add_argument("--email", default=os.getenv("MYREID_SMOKE_EMAIL", "smoke_user@example.com"))
    parser.add_argument("--password", default=os.getenv("MYREID_SMOKE_PASSWORD", "smoke_password"))
    return parser.parse_args()


def assert_ok(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    args = parse_args()
    database.init_database()
    client = TestClient(main.app)

    health = client.get("/api/v1/healthz")
    assert_ok(health.status_code == 200, f"healthz failed: {health.text}")

    ready = client.get("/api/v1/readyz")
    assert_ok(ready.status_code == 200, f"readyz failed: {ready.text}")

    code_response = client.post(
        "/api/v1/auth/email-code/request",
        json={"username": args.username, "email": args.email, "purpose": "register"},
    )
    if code_response.status_code == 400:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": args.username, "password": args.password},
        )
        assert_ok(login.status_code == 200, f"login failed: {login.text}")
        auth_payload = login.json()
    else:
        assert_ok(code_response.status_code == 200, f"email code request failed: {code_response.text}")
        code = code_response.json().get("debug_code")
        assert_ok(code, "EMAIL_CODE_DEV_MODE did not return debug_code")
        register = client.post(
            "/api/v1/auth/register",
            json={"username": args.username, "email": args.email, "password": args.password, "code": code},
        )
        assert_ok(register.status_code == 200, f"register failed: {register.text}")
        auth_payload = register.json()

    headers = {"Authorization": f"Bearer {auth_payload['access_token']}"}
    if auth_payload.get("refresh_token"):
        refresh = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {auth_payload['refresh_token']}"},
        )
        assert_ok(refresh.status_code == 200, f"refresh failed: {refresh.text}")
        auth_payload = refresh.json()
        headers = {"Authorization": f"Bearer {auth_payload['access_token']}"}

    me = client.get("/api/v1/auth/me", headers=headers)
    me_json = me.json()
    assert_ok(me.status_code == 200 and me_json["username"] == args.username and me_json.get("id"), f"me failed: {me.text}")

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Local Smoke Project", "description": "created by smoke test"},
    )
    assert_ok(project.status_code == 200, f"project create failed: {project.text}")
    project_id = project.json()["id"]

    project_detail = client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert_ok(project_detail.status_code == 200, f"project detail failed: {project_detail.text}")

    project_patch = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=headers,
        json={"description": "updated by smoke test"},
    )
    assert_ok(project_patch.status_code == 200, f"project patch failed: {project_patch.text}")

    projects = client.get("/api/v1/projects", headers=headers)
    assert_ok(projects.status_code == 200, f"project list failed: {projects.text}")
    assert_ok(any(item["id"] == project_id for item in projects.json()), "created project not found")

    metrics = client.get("/api/v1/metrics", headers=headers)
    assert_ok(metrics.status_code == 200, f"metrics failed: {metrics.text}")

    status = client.get(f"/api/v1/projects/{project_id}/status", headers=headers)
    assert_ok(status.status_code == 200 and "status" in status.json(), f"project status failed: {status.text}")

    preview = client.get(f"/api/v1/projects/{project_id}/preview", headers=headers)
    assert_ok(preview.status_code == 200 and "previews" in preview.json(), f"project preview failed: {preview.text}")

    media = client.get(f"/api/v1/projects/{project_id}/media", headers=headers)
    assert_ok(media.status_code == 200 and "media" in media.json(), f"project media list failed: {media.text}")

    members = client.get(f"/api/v1/projects/{project_id}/members", headers=headers)
    assert_ok(members.status_code == 200 and members.json(), f"project members list failed: {members.text}")

    jobs = client.get(f"/api/v1/projects/{project_id}/jobs", headers=headers)
    assert_ok(jobs.status_code == 200 and "jobs" in jobs.json(), f"project jobs list failed: {jobs.text}")

    audit = client.get(f"/api/v1/projects/{project_id}/audit", headers=headers)
    assert_ok(audit.status_code == 200 and "audit" in audit.json(), f"project audit list failed: {audit.text}")

    history = client.get("/api/v1/history", headers=headers)
    assert_ok(history.status_code == 200 and "history" in history.json(), f"history failed: {history.text}")

    db_status = client.get("/api/v1/database/status", headers=headers)
    assert_ok(db_status.status_code == 200 and db_status.json()["status"] == "ok", f"database status failed: {db_status.text}")

    observability = client.get("/api/v1/observability", headers=headers)
    assert_ok(observability.status_code in {200, 403}, f"observability unexpected: {observability.text}")

    delete_history = client.delete("/api/v1/history/0", headers=headers)
    assert_ok(delete_history.status_code == 200, f"history delete failed: {delete_history.text}")

    print(json.dumps({
        "status": "backend smoke ok",
        "username": args.username,
        "project_id": project_id,
        "database": db_status.json()["dialect"],
        "observability": observability.status_code,
    }, ensure_ascii=False))


if __name__ == "__main__":
    run()
