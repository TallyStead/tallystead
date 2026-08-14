import io
from urllib.parse import urlsplit

from minio import Minio

from app.config import settings

_memory_objects: dict[str, tuple[bytes, str]] = {}


def _memory_mode() -> bool:
    return settings.database_url.startswith("sqlite")


def _client() -> Minio:
    parsed = urlsplit(settings.object_store_endpoint)
    return Minio(
        parsed.netloc or parsed.path,
        access_key=settings.object_store_access_key,
        secret_key=settings.object_store_secret_key,
        secure=parsed.scheme == "https",
    )


def ensure_bucket() -> None:
    if _memory_mode():
        return
    client = _client()
    if not client.bucket_exists(settings.object_store_bucket):
        client.make_bucket(settings.object_store_bucket)


def put_object(key: str, content: bytes, content_type: str) -> None:
    if _memory_mode():
        _memory_objects[key] = (content, content_type)
        return
    ensure_bucket()
    _client().put_object(
        settings.object_store_bucket,
        key,
        io.BytesIO(content),
        length=len(content),
        content_type=content_type,
    )


def get_object(key: str) -> tuple[bytes, str]:
    if _memory_mode():
        return _memory_objects[key]
    response = _client().get_object(settings.object_store_bucket, key)
    try:
        return response.read(), response.headers.get("Content-Type", "application/octet-stream")
    finally:
        response.close()
        response.release_conn()


def remove_object(key: str | None) -> None:
    if not key:
        return
    if _memory_mode():
        _memory_objects.pop(key, None)
        return
    _client().remove_object(settings.object_store_bucket, key)
