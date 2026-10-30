"""Atomic create-only S3/MinIO storage; hashes are SHA-256, never S3 ETags."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

from qtrade_domain.objects import validate_object
from qtrade_domain.ports import ErrorCode, ObjectRef, PortError


def translated_error(error: BotoCoreError | ClientError) -> PortError:
    if isinstance(error, ConnectTimeoutError | ReadTimeoutError):
        return PortError(ErrorCode.TIMEOUT, "object store request timed out")
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        mapping = {
            "404": ErrorCode.NOT_FOUND,
            "NoSuchKey": ErrorCode.NOT_FOUND,
            "NoSuchBucket": ErrorCode.NOT_FOUND,
            "NotFound": ErrorCode.NOT_FOUND,
            "403": ErrorCode.UNAUTHORIZED,
            "AccessDenied": ErrorCode.UNAUTHORIZED,
            "InvalidAccessKeyId": ErrorCode.UNAUTHORIZED,
            "SignatureDoesNotMatch": ErrorCode.UNAUTHORIZED,
            "ExpiredToken": ErrorCode.UNAUTHORIZED,
            "InvalidToken": ErrorCode.UNAUTHORIZED,
            "PreconditionFailed": ErrorCode.CONFLICT,
            "412": ErrorCode.CONFLICT,
            "BadDigest": ErrorCode.CHECKSUM_MISMATCH,
            "SlowDown": ErrorCode.RATE_LIMITED,
            "Throttling": ErrorCode.RATE_LIMITED,
            "RequestTimeout": ErrorCode.TIMEOUT,
            "InvalidArgument": ErrorCode.INVALID_INPUT,
            "NotImplemented": ErrorCode.UNSUPPORTED,
        }
        return PortError(mapping.get(code, ErrorCode.UNAVAILABLE), "object store request failed")
    return PortError(ErrorCode.UNAVAILABLE, "object store connection unavailable")


class S3ObjectStore:
    def __init__(self, client: S3Client, bucket: str):
        if not bucket:
            raise PortError(ErrorCode.INVALID_INPUT, "object store bucket is required")
        self._client = client
        self._bucket = bucket

    def ensure_bucket(self) -> None:
        """Create the configured bucket if absent; call only from explicit local setup."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise translated_error(error) from None
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except (BotoCoreError, ClientError) as create_error:
                if isinstance(create_error, ClientError) and create_error.response.get(
                    "Error", {}
                ).get("Code") in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    return
                raise translated_error(create_error) from None
        except BotoCoreError as error:
            raise translated_error(error) from None

    @classmethod
    def connect(
        cls,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        allow_http: bool = False,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> S3ObjectStore:
        if endpoint_url is not None:
            endpoint = urlsplit(endpoint_url)
            if (
                endpoint.scheme not in ({"http", "https"} if allow_http else {"https"})
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or endpoint.path not in {"", "/"}
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid object store endpoint")
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=3,
                read_timeout=10,
                retries={"mode": "standard", "total_max_attempts": 2},
                s3={"addressing_style": "path"},
            ),
        )
        return cls(client, bucket)

    @staticmethod
    def _key(workspace_id: UUID, key: str) -> str:
        return f"workspaces/{workspace_id}/{key}"

    def put(
        self,
        workspace_id: UUID,
        key: str,
        content: bytes,
        *,
        content_type: str,
        expected_sha256: str,
    ) -> ObjectRef:
        validate_object(key, content, expected_sha256)
        if (
            not content_type.strip()
            or len(content_type) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in content_type)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid content type")
        ref = ObjectRef(workspace_id, key, expected_sha256, len(content), content_type)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._key(workspace_id, key),
                Body=content,
                ContentType=content_type,
                Metadata={"sha256": expected_sha256},
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(expected_sha256)).decode("ascii"),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}:
                existing = self.stat(workspace_id, key)
                if existing != ref:
                    raise PortError(ErrorCode.CONFLICT, "immutable object already exists") from None
                self.get(workspace_id, key, expected_sha256=expected_sha256)
                return existing
            raise translated_error(error) from None
        except BotoCoreError as error:
            raise translated_error(error) from None
        return ref

    def stat(self, workspace_id: UUID, key: str) -> ObjectRef:
        validate_object(key, None, "0" * 64)
        try:
            response = self._client.head_object(
                Bucket=self._bucket, Key=self._key(workspace_id, key)
            )
        except (BotoCoreError, ClientError) as error:
            raise translated_error(error) from None
        digest = response.get("Metadata", {}).get("sha256", "")
        try:
            validate_object(key, None, digest)
        except PortError:
            raise PortError(
                ErrorCode.CHECKSUM_MISMATCH, "object integrity metadata missing"
            ) from None
        return ObjectRef(
            workspace_id, key, digest, response["ContentLength"], response.get("ContentType", "")
        )

    def get(self, workspace_id: UUID, key: str, *, expected_sha256: str) -> bytes:
        validate_object(key, None, expected_sha256)
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=self._key(workspace_id, key)
            )
            body = response["Body"]
            try:
                content = body.read()
            finally:
                body.close()
        except (BotoCoreError, ClientError) as error:
            raise translated_error(error) from None
        if response.get("Metadata", {}).get("sha256") != expected_sha256 or response[
            "ContentLength"
        ] != len(content):
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "object integrity metadata mismatch")
        validate_object(key, content, expected_sha256)
        return content
