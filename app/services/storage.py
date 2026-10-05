from __future__ import annotations

import hashlib
import io
from contextlib import suppress
from dataclasses import dataclass
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from app.config import get_settings


@dataclass(slots=True)
class StoredObject:
    bucket: str
    object_key: str
    sha256: str
    size_bytes: int


class StorageService:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_buckets(self) -> None:
        for bucket in (self.settings.raw_bucket, self.settings.result_bucket):
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def put_stream(
        self,
        bucket: str,
        object_key: str,
        stream: BinaryIO,
        size: int,
        content_type: str,
    ) -> None:
        self.client.put_object(bucket, object_key, stream, size, content_type=content_type)

    def put_bytes(
        self, bucket: str, object_key: str, payload: bytes, content_type: str
    ) -> StoredObject:
        stream = io.BytesIO(payload)
        digest = hashlib.sha256(payload).hexdigest()
        self.put_stream(bucket, object_key, stream, len(payload), content_type)
        return StoredObject(bucket, object_key, digest, len(payload))

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        response = self.client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def download_to(self, bucket: str, object_key: str, target: BinaryIO) -> None:
        response = self.client.get_object(bucket, object_key)
        try:
            for chunk in response.stream(1024 * 1024):
                target.write(chunk)
        finally:
            response.close()
            response.release_conn()

    def remove(self, bucket: str, object_key: str) -> None:
        with suppress(S3Error):
            self.client.remove_object(bucket, object_key)

    def ready(self) -> bool:
        try:
            return self.client.bucket_exists(self.settings.raw_bucket)
        except Exception:
            return False


storage = StorageService()
