"""Offline S3 current-object snapshot, checksummed and restored only to an empty bucket.

Use with backup_restore.sh in the SAME stopped-writer maintenance window.
Version history, delete markers and IAM/lifecycle policies need provider backups.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FastAPIBackend"))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if not args.writers_stopped:
        parser.error("Confirm all writers stopped using --writers-stopped")
    from app.core.config import get_settings
    import boto3
    settings = get_settings()
    if settings.object_storage_mode not in {"s3", "minio"}:
        parser.error("Requires S3/MinIO configuration")
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key_id or None, aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region)
    root, bucket = args.directory.resolve(), settings.s3_bucket
    if args.operation == "backup":
        root.mkdir(parents=True, exist_ok=False)
        manifest = []
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = hashlib.sha256(key.encode()).hexdigest()
                target = root / name
                client.download_file(bucket, key, str(target))
                head = client.head_object(Bucket=bucket, Key=key)
                metadata = {k: head[k] for k in ("ContentType", "ContentDisposition", "ContentEncoding", "CacheControl", "Metadata") if k in head}
                manifest.append({"key": key, "file": name, "size": target.stat().st_size, "sha256": sha256(target), "metadata": metadata})
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        if client.list_objects_v2(Bucket=bucket, MaxKeys=1).get("KeyCount", 0):
            parser.error("Refusing to restore over a nonempty bucket")
        # Verify every object before uploading anything; reject manifest traversal.
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for obj in manifest:
            name = hashlib.sha256(obj["key"].encode()).hexdigest()
            if obj["file"] != name or sha256(root / name) != obj["sha256"] or (root / name).stat().st_size != obj["size"]:
                raise ValueError("Invalid snapshot checksum or object path")
        for obj in manifest:
            metadata = {k: v for k, v in obj.get("metadata", {}).items() if k in
                        {"ContentType", "ContentDisposition", "ContentEncoding", "CacheControl", "Metadata"}}
            client.upload_file(str(root / obj["file"]), bucket, obj["key"], ExtraArgs=metadata)
    print(f"{args.operation}: {len(manifest)} objects; no credentials written to snapshot")


if __name__ == "__main__":
    main()
