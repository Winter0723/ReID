from pathlib import Path
import argparse
import json
import os
import tempfile
import time

import httpx
from PIL import Image, ImageDraw


TMP_IMAGE = Path(
    os.getenv(
        "MYREID_BUSINESS_SMOKE_IMAGE",
        str(Path(tempfile.gettempdir()) / "myreid_business_smoke_person.jpg"),
    )
)


def make_test_image():
    TMP_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (128, 256), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse([48, 10, 80, 42], fill="tan")
    draw.rectangle([44, 42, 84, 210], fill="blue")
    draw.rectangle([44, 210, 58, 250], fill="black")
    draw.rectangle([70, 210, 84, 250], fill="black")
    image.save(TMP_IMAGE)


def parse_args():
    parser = argparse.ArgumentParser(description="Run a live HTTP business smoke test against MyReID.")
    parser.add_argument("--base-url", default=os.getenv("MYREID_BASE_URL", "http://127.0.0.1:2889/api/v1"))
    parser.add_argument("--username", default=os.getenv("MYREID_BUSINESS_SMOKE_USER", "local_business_user"))
    parser.add_argument("--email", default=os.getenv("MYREID_BUSINESS_SMOKE_EMAIL", "local_business_user@example.com"))
    parser.add_argument("--password", default=os.getenv("MYREID_BUSINESS_SMOKE_PASSWORD", "local_business_password"))
    parser.add_argument("--polls", type=int, default=int(os.getenv("MYREID_BUSINESS_SMOKE_POLLS", "80")))
    parser.add_argument("--sleep-seconds", type=float, default=float(os.getenv("MYREID_BUSINESS_SMOKE_SLEEP", "0.5")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("MYREID_BUSINESS_SMOKE_TIMEOUT", "180")))
    parser.add_argument("--video-path", default=os.getenv("MYREID_BUSINESS_SMOKE_VIDEO", ""))
    return parser.parse_args()


def wait_for_job(client, base_url: str, headers: dict, job_id: str, polls: int, sleep_seconds: float):
    job = {}
    for _ in range(polls):
        job_resp = client.get(f"{base_url}/jobs/{job_id}", headers=headers)
        job_resp.raise_for_status()
        job = job_resp.json()
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(sleep_seconds)
    if job.get("status") != "succeeded":
        raise RuntimeError(f"index job did not succeed: {job}")
    return job


def upload_media(client, base_url: str, headers: dict, project_id: int, path: Path, content_type: str, args):
    with path.open("rb") as file_obj:
        upload = client.post(
            f"{base_url}/projects/{project_id}/media",
            headers=headers,
            files={"file": (path.name, file_obj, content_type)},
        )
    upload.raise_for_status()
    media = upload.json()
    if media.get("job_id"):
        job = wait_for_job(client, base_url, headers, media["job_id"], args.polls, args.sleep_seconds)
    else:
        job = {"status": media["status"]}
    return media, job


def main():
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    make_test_image()
    client = httpx.Client(timeout=args.timeout)

    health = client.get(f"{base_url}/healthz")
    health.raise_for_status()
    ready = client.get(f"{base_url}/readyz")
    ready.raise_for_status()

    code_response = client.post(
        f"{base_url}/auth/email-code/request",
        json={"username": args.username, "email": args.email, "purpose": "register"},
    )
    if code_response.status_code == 400:
        auth = client.post(
            f"{base_url}/auth/login",
            json={"username": args.username, "password": args.password},
        )
    else:
        code_response.raise_for_status()
        code = code_response.json().get("debug_code")
        if not code:
            raise RuntimeError("Registration smoke requires EMAIL_CODE_DEV_MODE=1 on the running backend")
        auth = client.post(
            f"{base_url}/auth/register",
            json={"username": args.username, "email": args.email, "password": args.password, "code": code},
        )
    auth.raise_for_status()
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

    project = client.post(
        f"{base_url}/projects",
        headers=headers,
        json={"name": "Local PostgreSQL Business Smoke", "description": "upload, index, search"},
    )
    project.raise_for_status()
    project_id = project.json()["id"]

    media, job = upload_media(client, base_url, headers, project_id, TMP_IMAGE, "image/jpeg", args)
    video_media = None
    if args.video_path:
        video_media, _ = upload_media(client, base_url, headers, project_id, Path(args.video_path), "video/mp4", args)

    media_list = client.get(f"{base_url}/projects/{project_id}/media", headers=headers)
    media_list.raise_for_status()
    if not media_list.json()["media"]:
        raise RuntimeError("uploaded media not present in media list")

    search = client.post(
        f"{base_url}/search/text",
        headers=headers,
        json={"project_id": project_id, "query_text": "a blue person", "top_k": 5},
    )
    search.raise_for_status()
    results = search.json()["results"]
    if not results:
        raise RuntimeError("search returned no results")
    task_id = search.json()["task_id"]

    history = client.get(f"{base_url}/history/{task_id}", headers=headers)
    history.raise_for_status()
    report = client.get(f"{base_url}/search/tasks/{task_id}/report?format=csv", headers=headers)
    report.raise_for_status()

    db_status = client.get(f"{base_url}/database/status", headers=headers)
    db_status.raise_for_status()
    print(
        json.dumps(
            {
                "status": "business http smoke ok",
                "base_url": base_url,
                "project_id": project_id,
                "media_id": media["media_id"],
                "video_media_id": video_media["media_id"] if video_media else None,
                "job_status": job["status"],
                "task_id": task_id,
                "results": len(results),
                "database": db_status.json()["dialect"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
