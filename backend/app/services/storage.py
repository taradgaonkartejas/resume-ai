import functools

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.db import DATA_DIR


@functools.lru_cache(maxsize=1)
def _backend():
    """Probe MinIO once per process. Returns (client_or_None, mode)."""
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=2,
                read_timeout=5,
                retries={"max_attempts": 1},
                s3={"addressing_style": "path"},  # required for MinIO
            ),
        )
        client.list_buckets()
        return client, "minio"
    except (BotoCoreError, ClientError, OSError, Exception):
        if not settings.allow_disk_fallback:
            raise
        return None, "disk"


def storage_mode() -> str:
    return _backend()[1]


def object_key(user_id, resume_id, filename: str) -> str:
    return f"{user_id}/{resume_id}/{filename}"


def _disk_path(bucket: str, key: str):
    path = DATA_DIR / bucket / key
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def put_object(
    bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
) -> str:
    client, mode = _backend()
    if mode == "minio":
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    else:
        _disk_path(bucket, key).write_bytes(data)
    return f"{bucket}/{key}"


def get_object(bucket: str, key: str) -> bytes:
    client, mode = _backend()
    if mode == "minio":
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return _disk_path(bucket, key).read_bytes()


def object_exists(bucket: str, key: str) -> bool:
    client, mode = _backend()
    if mode == "minio":
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False
    return _disk_path(bucket, key).exists()


def delete_object(bucket: str, key: str) -> None:
    client, mode = _backend()
    if mode == "minio":
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except ClientError:
            pass
    else:
        path = _disk_path(bucket, key)
        if path.exists():
            path.unlink()


def delete_prefix(bucket: str, prefix: str) -> int:
    """Remove every object under a prefix. Used by resume deletion."""
    client, mode = _backend()
    removed = 0
    if mode == "minio":
        try:
            listing = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in listing.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=obj["Key"])
                removed += 1
        except ClientError:
            pass
    else:
        base = DATA_DIR / bucket / prefix
        if base.exists():
            for path in base.rglob("*"):
                if path.is_file():
                    path.unlink()
                    removed += 1
    return removed
